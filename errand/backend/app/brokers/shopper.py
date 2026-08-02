"""Shopper broker — drives a REAL browser to shop a merchant and check out.

The browser runs either on Cloudflare Browser Rendering (headless Chromium in
Cloudflare's edge, reached over CDP) or on a LOCAL Playwright Chromium. Which one
is used is decided per target URL:

  * Public http(s) URLs  → Cloudflare Browser Rendering (when credentials exist),
    because Cloudflare's remote browser can reach the public internet.
  * localhost / 127.0.0.1 / file:// URLs → LOCAL Playwright Chromium, because a
    remote Cloudflare browser cannot see a store served on the developer's box.

This keeps the demo (a controlled storefront served on localhost) reliable while
still exercising the exact same Playwright automation code paths that Cloudflare
uses in production. The Cloudflare CDP endpoint is the one documented at
https://developers.cloudflare.com/browser-run/cdp/playwright/ :

    wss://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/browser-rendering/devtools/browser?keep_alive=600000

authenticated with `Authorization: Bearer <API_TOKEN>` and connected via
Playwright's ``connect_over_cdp``.

Implements the ShopperBroker Protocol from app.contracts exactly:
    async build_cart(merchant_url, intent, context) -> CartResult
    async complete_checkout(checkout, credential) -> OrderResult
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from typing import AsyncIterator, Awaitable, Literal, TypeVar
from urllib.parse import urlparse, urlsplit, urlunsplit, parse_qsl, urlencode

from playwright.async_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from app.contracts import (
    CartItem,
    CartResult,
    CheckoutState,
    OrderResult,
    PaymentCredential,
    PurchaseContext,
)

logger = logging.getLogger(__name__)

BrowserMode = Literal["cloudflare", "local"]
T = TypeVar("T")

# ── Resilience knobs ────────────────────────────────────────────────────────────
# Every browser action gets an explicit timeout so a stuck page can never hang the
# SSE stream. Values are milliseconds (Playwright's unit) unless suffixed _S.
_NAV_TIMEOUT_MS = 30_000        # page.goto / page.reload
_SELECTOR_TIMEOUT_MS = 10_000   # wait_for_selector readiness probe
_ACTION_TIMEOUT_MS = 10_000     # click / fill / eval
_CONFIRMATION_TIMEOUT_MS = 15_000  # wait for the order confirmation to render
_CONNECT_TIMEOUT_MS = 30_000    # CDP connect / local launch

# Empty-DOM / not-ready ladder (browser-use service.py:514-544): after navigating,
# if the expected element isn't there, wait → reload → wait, bounded, then raise a
# structured error. Attempt 1 = initial nav; later attempts pause then reload.
_MAX_READY_ATTEMPTS = 3
_EMPTY_DOM_WAIT_S = 3.0   # pause before the first reload
_RELOAD_WAIT_S = 5.0      # pause before any subsequent reload


class ShopperError(RuntimeError):
    """Structured, human-readable failure the orchestrator can surface directly.

    Carries which ``step`` failed (e.g. "build_cart") and, when relevant, the URL
    involved, so a ``cart.failed`` audit event reads cleanly instead of leaking a
    raw Playwright stack trace.
    """

    def __init__(self, step: str, message: str, *, url: str | None = None) -> None:
        self.step = step
        self.url = url
        detail = f"[shopper:{step}] {message}"
        if url:
            detail = f"{detail} (url={url})"
        super().__init__(detail)


def _short(exc: BaseException, limit: int = 200) -> str:
    """One-line, length-bounded rendering of an exception for error messages."""
    text = f"{type(exc).__name__}: {exc}".splitlines()[0]
    return text if len(text) <= limit else text[: limit - 1] + "…"


_warned: set[str] = set()


def _warn_once(key: str, msg: str) -> None:
    """De-dupe warnings so a bounded retry loop can't spam the logs."""
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(msg)


async def _page_appears_empty(page: Page) -> bool:
    """Empty-DOM health check: True when the page rendered no real content.

    Mirrors browser-use's ``_page_appears_empty`` (service.py:514-544): a
    navigation can "succeed" yet leave a blank document (SPA not hydrated,
    anti-bot interstitial). Stripping tags and checking for any text is a robust
    proxy for their DOM ``llm_representation()`` check.
    """
    try:
        html = await asyncio.wait_for(page.content(), timeout=_ACTION_TIMEOUT_MS / 1000)
    except (asyncio.TimeoutError, PlaywrightTimeoutError, PlaywrightError):
        return True
    return not re.sub(r"<[^>]+>", " ", html).strip()


def _settings_get(name: str) -> str:
    """Read a value from app.config.settings, best-effort.

    Env vars (via os.getenv) take precedence in the constructor; this is the
    fallback so the broker still finds Cloudflare creds loaded from errand/.env
    by pydantic-settings, without hard-failing if config can't be imported.
    """
    try:
        from app.config import settings

        return getattr(settings, name, "") or ""
    except Exception:  # noqa: BLE001
        return ""


# The CDP WebSocket endpoint, copied from the current doc's own template. The
# path still reads `browser-rendering` after the product was renamed to Browser
# Run — that is what the doc prints, so it is a live path and not a leftover to
# tidy; rewriting it to `browser-run` would dial an endpoint that does not exist.
# keep_alive is milliseconds, and 600000 is the ceiling rather than a taste:
# a browser times out after 60s of inactivity by default and keep_alive raises
# that to at most 10 minutes, so a larger number buys nothing.
# https://developers.cloudflare.com/browser-run/cdp/playwright/
# https://developers.cloudflare.com/browser-run/limits/
_CF_WS_TEMPLATE = (
    "wss://api.cloudflare.com/client/v4/accounts/{account_id}"
    "/browser-rendering/devtools/browser?keep_alive=600000"
)


def _is_local_target(url: str) -> bool:
    """True when the URL points at the local machine / filesystem.

    Cloudflare's remote browser cannot reach these, so they force the local path.
    """
    if url.startswith("file://"):
        return True
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", ""}


def _checkout_url_for(merchant_url: str, total_cents: int) -> str:
    """Derive the checkout URL from the storefront URL, pinning the total.

    The demo store's index page links to ``checkout.html``; we build the same
    sibling URL and attach ``total_cents`` so a relaunched checkout page (a fresh
    browser context with no shared localStorage) still renders the real amount.
    """
    parts = urlsplit(merchant_url)
    path = parts.path
    if path.endswith("/") or path == "":
        new_path = path.rstrip("/") + "/checkout.html"
    else:
        # replace the last path segment (e.g. index.html) with checkout.html
        new_path = path.rsplit("/", 1)[0] + "/checkout.html"
    query = dict(parse_qsl(parts.query))
    query["total_cents"] = str(total_cents)
    return urlunsplit((parts.scheme, parts.netloc, new_path, urlencode(query), ""))


class CloudflareShopperBroker:
    """Real-browser shopper. Cloudflare Browser Rendering with a local fallback.

    Constructed with no arguments by the broker registry; credentials are read
    from the environment (same names as app.config: CLOUDFLARE_ACCOUNT_ID /
    CLOUDFLARE_API_TOKEN). Passing them explicitly is supported for tests.
    """

    def __init__(
        self,
        account_id: str | None = None,
        api_token: str | None = None,
        *,
        force_local: bool = False,
        nav_timeout_ms: int = _NAV_TIMEOUT_MS,
        selector_timeout_ms: int = _SELECTOR_TIMEOUT_MS,
        action_timeout_ms: int = _ACTION_TIMEOUT_MS,
        confirmation_timeout_ms: int = _CONFIRMATION_TIMEOUT_MS,
        empty_dom_wait_s: float = _EMPTY_DOM_WAIT_S,
        reload_wait_s: float = _RELOAD_WAIT_S,
        max_ready_attempts: int = _MAX_READY_ATTEMPTS,
    ) -> None:
        self._account_id = (
            account_id
            or os.getenv("CLOUDFLARE_ACCOUNT_ID")
            or _settings_get("cloudflare_account_id")
        )
        self._api_token = (
            api_token
            or os.getenv("CLOUDFLARE_API_TOKEN")
            or _settings_get("cloudflare_api_token")
        )
        self._force_local = force_local
        # Per-action timeouts (ms) and the not-ready ladder's waits (s). Defaults
        # match the module constants; overridable so tests can run the failure
        # ladder fast instead of waiting the full production budget.
        self._nav_timeout_ms = nav_timeout_ms
        self._selector_timeout_ms = selector_timeout_ms
        self._action_timeout_ms = action_timeout_ms
        self._confirmation_timeout_ms = confirmation_timeout_ms
        self._empty_dom_wait_s = empty_dom_wait_s
        self._reload_wait_s = reload_wait_s
        self._max_ready_attempts = max(1, max_ready_attempts)

    # ── browser session management ────────────────────────────────────────────

    def _mode_for(self, url: str) -> BrowserMode:
        if self._force_local or _is_local_target(url):
            return "local"
        if self._account_id and self._api_token:
            return "cloudflare"
        return "local"

    @contextlib.asynccontextmanager
    async def _page(self, target_url: str) -> AsyncIterator[tuple[Page, BrowserMode]]:
        """Open a browser + page for ``target_url`` and clean everything up.

        Yields ``(page, mode)`` so callers can report which backend ran.
        """
        mode = self._mode_for(target_url)
        async with async_playwright() as pw:
            if mode == "cloudflare":
                ws = _CF_WS_TEMPLATE.format(account_id=self._account_id)
                browser = await pw.chromium.connect_over_cdp(
                    ws,
                    headers={"Authorization": f"Bearer {self._api_token}"},
                    timeout=_CONNECT_TIMEOUT_MS,
                )
                # Cloudflare hands back a live context; reuse it.
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    yield page, mode
                finally:
                    with contextlib.suppress(Exception):
                        await browser.close()
            else:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                try:
                    yield page, mode
                finally:
                    with contextlib.suppress(Exception):
                        await context.close()
                    with contextlib.suppress(Exception):
                        await browser.close()

    # ── resilience primitives ─────────────────────────────────────────────────

    async def _guard(self, step: str, what: str, coro: Awaitable[T], *, url: str | None = None) -> T:
        """Run one browser action under an explicit timeout, wrapping failures.

        A per-action ``asyncio.wait_for`` backstops Playwright's own timeout so a
        stuck action can never hang the SSE stream; any timeout/exception becomes a
        structured ``ShopperError`` naming the step and action instead of a raw
        Playwright stack.
        """
        budget_s = self._action_timeout_ms / 1000 + 5.0  # cushion over PW's own timeout
        try:
            return await asyncio.wait_for(coro, timeout=budget_s)
        except (asyncio.TimeoutError, PlaywrightTimeoutError) as exc:
            raise ShopperError(step, f"timed out while {what}", url=url) from exc
        except ShopperError:
            raise
        except PlaywrightError as exc:
            raise ShopperError(step, f"failed while {what}: {_short(exc)}", url=url) from exc

    async def _goto_ready(self, page: Page, url: str, ready_selector: str, step: str) -> None:
        """Navigate to ``url`` and confirm ``ready_selector`` renders.

        Empty-DOM / not-ready ladder (browser-use service.py:514-544): navigate,
        then up to ``_max_ready_attempts`` times try to see the expected element;
        on empty/missing DOM, wait → reload → wait and retry. If it never appears,
        raise a structured ``ShopperError`` — never hang, never a bare stack.
        """
        for attempt in range(1, self._max_ready_attempts + 1):
            if attempt == 1:
                await self._guard(
                    step, f"navigating to {url}",
                    page.goto(url, wait_until="networkidle", timeout=self._nav_timeout_ms),
                    url=url,
                )
            else:
                # not ready yet: pause, then reload and re-probe.
                pause = self._empty_dom_wait_s if attempt == 2 else self._reload_wait_s
                _warn_once(
                    f"{step}:{url}:{attempt}",
                    f"[shopper:{step}] {ready_selector!r} not ready on {url}; "
                    f"waiting {pause}s then reload (attempt {attempt}/{self._max_ready_attempts})",
                )
                await asyncio.sleep(pause)
                with contextlib.suppress(asyncio.TimeoutError, PlaywrightTimeoutError, PlaywrightError):
                    await asyncio.wait_for(
                        page.reload(wait_until="networkidle", timeout=self._nav_timeout_ms),
                        timeout=self._nav_timeout_ms / 1000 + 5.0,
                    )

            # readiness probe: is the expected element actually there?
            try:
                await page.wait_for_selector(ready_selector, timeout=self._selector_timeout_ms)
                if not await _page_appears_empty(page):
                    return  # ready
            except (PlaywrightTimeoutError, PlaywrightError):
                pass  # fall through to the next ladder rung

        raise ShopperError(
            step,
            f"page loaded but expected element {ready_selector!r} never rendered "
            f"after {self._max_ready_attempts} attempts (empty DOM / not ready)",
            url=url,
        )

    # ── ShopperBroker Protocol ────────────────────────────────────────────────

    async def build_cart(
        self, merchant_url: str, intent: str, context: PurchaseContext
    ) -> CartResult:
        """Navigate the store, add items honoring budget + brand rules, read the
        real DOM total, and return a resumable CheckoutState."""
        preferred = _preferred_brands(context.rules)

        try:
            async with self._page(merchant_url) as (page, _mode):
                # navigate + not-ready ladder: guarantees products rendered.
                await self._goto_ready(page, merchant_url, "[data-product-id]", "build_cart")

                products = await self._guard(
                    "build_cart", "reading the product catalog",
                    page.eval_on_selector_all(
                        "[data-product-id]",
                        """(nodes) => nodes.map((n) => ({
                            id: n.dataset.productId,
                            brand: n.dataset.brand || '',
                            price_cents: parseInt(n.dataset.priceCents || '0', 10),
                            name: (n.querySelector('[data-name]') || {}).textContent || ''
                        }))""",
                    ),
                    url=merchant_url,
                )
                if not products:
                    raise ShopperError(
                        "build_cart", "no products found on the storefront", url=merchant_url
                    )

                plan = _select_items(
                    products, context.budget_cents, preferred, context.rules
                )

                # Actually click the add-to-cart buttons — real browser interaction.
                for prod_id, qty in plan:
                    for _ in range(qty):
                        await self._guard(
                            "build_cart", f"adding {prod_id} to cart",
                            page.click(
                                f'button[data-add="{prod_id}"]',
                                timeout=self._action_timeout_ms,
                            ),
                            url=merchant_url,
                        )

                # Read the authoritative total straight from the DOM.
                total_cents = await self._guard(
                    "build_cart", "reading the cart total",
                    page.eval_on_selector(
                        "#cart-total", "(el) => parseInt(el.dataset.totalCents || '0', 10)"
                    ),
                    url=merchant_url,
                )

                # Build the item list from what actually landed in the cart.
                id_to_prod = {p["id"]: p for p in products}
                items: list[CartItem] = []
                for prod_id, qty in plan:
                    if qty <= 0:
                        continue
                    p = id_to_prod[prod_id]
                    items.append(
                        CartItem(name=p["name"].strip(), qty=qty, price_cents=int(p["price_cents"]))
                    )
        except ShopperError:
            raise
        except (asyncio.TimeoutError, PlaywrightTimeoutError) as exc:
            raise ShopperError("build_cart", "browser session timed out", url=merchant_url) from exc
        except PlaywrightError as exc:
            raise ShopperError(
                "build_cart", f"browser session failed: {_short(exc)}", url=merchant_url
            ) from exc

        session_ref = _checkout_url_for(merchant_url, total_cents)
        return CartResult(
            items=items,
            total_cents=int(total_cents),
            checkout=CheckoutState(
                merchant_url=merchant_url, items=items, session_ref=session_ref
            ),
        )

    async def complete_checkout(
        self, checkout: CheckoutState, credential: PaymentCredential
    ) -> OrderResult:
        """Open the pinned checkout URL, type the Prava credential into the form,
        place the order, and scrape the confirmation text + order id."""
        checkout_url = checkout.session_ref or _checkout_url_for(
            checkout.merchant_url,
            sum(i.qty * i.price_cents for i in checkout.items),
        )

        try:
            async with self._page(checkout_url) as (page, _mode):
                # navigate + not-ready ladder: guarantees the form rendered.
                await self._goto_ready(page, checkout_url, "#checkout-form", "complete_checkout")

                # Type the one-time Prava card credential into the real form fields.
                fields = (
                    ("#card-number", credential.token, "card number"),
                    ("#expiry-month", credential.expiry_month, "expiry month"),
                    ("#expiry-year", credential.expiry_year, "expiry year"),
                    ("#cvv", credential.dynamic_cvv, "CVV"),
                )
                for selector, value, label in fields:
                    await self._guard(
                        "complete_checkout", f"filling the {label} field",
                        page.fill(selector, value, timeout=self._action_timeout_ms),
                        url=checkout_url,
                    )

                await self._guard(
                    "complete_checkout", "clicking place-order",
                    page.click("#place-order", timeout=self._action_timeout_ms),
                    url=checkout_url,
                )

                # Wait for the confirmation to render, then scrape it.
                await self._guard(
                    "complete_checkout", "waiting for the order confirmation",
                    page.wait_for_selector(
                        "#confirmation.show", timeout=self._confirmation_timeout_ms
                    ),
                    url=checkout_url,
                )
                order_id = await self._guard(
                    "complete_checkout", "reading the order id",
                    page.eval_on_selector(
                        "#confirmation", "(el) => el.dataset.orderId || ''"
                    ),
                    url=checkout_url,
                )
                confirmation_text = (
                    await self._guard(
                        "complete_checkout", "reading the confirmation text",
                        page.eval_on_selector("#confirmation", "(el) => el.innerText"),
                        url=checkout_url,
                    )
                ).strip()
        except ShopperError:
            raise
        except (asyncio.TimeoutError, PlaywrightTimeoutError) as exc:
            raise ShopperError(
                "complete_checkout", "browser session timed out", url=checkout_url
            ) from exc
        except PlaywrightError as exc:
            raise ShopperError(
                "complete_checkout", f"browser session failed: {_short(exc)}", url=checkout_url
            ) from exc

        if not order_id:
            # fall back to parsing the id out of the confirmation copy
            m = re.search(r"ORD-\d+", confirmation_text)
            order_id = m.group(0) if m else ""

        if not order_id:
            raise ShopperError(
                "complete_checkout",
                "order placed but no order id could be read from the confirmation",
                url=checkout_url,
            )

        return OrderResult(order_id=order_id, confirmation_text=confirmation_text)


# ── selection helpers ──────────────────────────────────────────────────────────


def _preferred_brands(rules: list[str]) -> list[str]:
    """Pull candidate brand names out of free-text context rules.

    e.g. "Prefer Blue Bottle/Clif/LaCroix" → ["blue bottle", "clif", "lacroix"].
    Best-effort: rules are prose, so we split on common separators and keep short
    alpha tokens. Missing/unparseable rules just mean "no brand preference".
    """
    brands: list[str] = []
    for rule in rules:
        low = rule.lower()
        if "prefer" not in low and "brand" not in low:
            continue
        # take the part after 'prefer' if present
        segment = low.split("prefer", 1)[1] if "prefer" in low else low
        for token in re.split(r"[\/,]| and | or ", segment):
            token = token.strip(" .;:")
            # drop leading verbs / filler
            token = re.sub(r"^(the|a|an)\s+", "", token)
            if 2 <= len(token) <= 24 and re.search(r"[a-z]", token):
                brands.append(token)
    return brands


def _is_disallowed(name: str, brand: str, rules: list[str]) -> bool:
    """Honor simple negative rules like 'No energy drinks'."""
    hay = f"{name} {brand}".lower()
    for rule in rules:
        low = rule.lower()
        m = re.search(r"\bno\s+([a-z][a-z \-]{2,30})", low)
        if m:
            banned = m.group(1).strip()
            # match on the salient noun(s); loose contains check
            for word in banned.split():
                if len(word) >= 4 and word in hay:
                    return True
    return False


def _select_items(
    products: list[dict], budget_cents: int, preferred: list[str], rules: list[str]
) -> list[tuple[str, int]]:
    """Choose (product_id, qty) pairs that respect the budget and prefer brands.

    Strategy: rank preferred-brand products first, add up to 2 of each while the
    running total stays within budget, then top up with other allowed products at
    qty 1. Never exceeds budget; always returns at least one item if any fits.
    Negative rules (e.g. "No energy drinks") drop matching products entirely.
    """
    def prefers(p: dict) -> bool:
        hay = f"{p['brand']} {p['name']}".lower()
        return any(b in hay for b in preferred)

    allowed = [
        p for p in products if not _is_disallowed(p["name"], p["brand"], rules)
    ]

    ranked = sorted(allowed, key=lambda p: (0 if prefers(p) else 1, p["price_cents"]))

    plan: dict[str, int] = {}
    total = 0
    # first pass: preferred brands, up to 2 each
    for p in ranked:
        if not prefers(p):
            continue
        for _ in range(2):
            if total + p["price_cents"] <= budget_cents:
                plan[p["id"]] = plan.get(p["id"], 0) + 1
                total += p["price_cents"]
    # second pass: any remaining allowed product, qty 1, to use budget sensibly
    for p in ranked:
        if p["id"] in plan:
            continue
        if total + p["price_cents"] <= budget_cents:
            plan[p["id"]] = 1
            total += p["price_cents"]

    # guarantee at least one item if the cheapest fits
    if not plan and ranked:
        cheapest = min(ranked, key=lambda p: p["price_cents"])
        if cheapest["price_cents"] <= budget_cents:
            plan[cheapest["id"]] = 1

    return [(pid, qty) for pid, qty in plan.items() if qty > 0]
