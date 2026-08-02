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

    async def agentic_build_cart(
        self,
        merchant_url: str,
        intent: str,
        context: PurchaseContext,
        *,
        decide,
        on_frame=None,
        max_actions: int = 16,
    ) -> CartResult:
        """The LLM DRIVES this cart: it observes the shelf + cart and chooses each
        add/remove, instead of the fixed budget-filler in build_cart. Every add is
        still checked against policy + budget by run_agentic_shop, so the model
        picks WHAT to buy but cannot break a spend rule.

        `decide` is the injected model step (see app.routers.chat). `on_frame`, if
        given, is awaited with a JPEG screenshot (bytes) + a caption after each
        action so the caller can stream the browser live. The authoritative total
        is still read from #cart-total off the real DOM at the end — never trusted
        from the loop — so the money-path invariants downstream do not move.
        """
        from app.orchestrator.agentic_shop import Product, run_agentic_shop

        try:
            async with self._page(merchant_url) as (page, _mode):
                await self._goto_ready(page, merchant_url, "[data-product-id]", "build_cart")

                surface = _PlaywrightShopSurface(page, self, merchant_url)

                async def on_step(step: str, detail: str) -> None:
                    # A frame is best-effort: a failed screenshot must never abort
                    # a real shopping run, so it is swallowed and the loop goes on.
                    if on_frame is None:
                        return
                    shot: bytes | None = None
                    with contextlib.suppress(Exception):
                        shot = await asyncio.wait_for(
                            page.screenshot(type="jpeg", quality=45), timeout=8.0
                        )
                    await on_frame(step, detail, shot)

                plan = await run_agentic_shop(
                    surface,
                    intent=intent,
                    budget_cents=context.budget_cents,
                    rules=context.rules,
                    decide=decide,
                    max_actions=max_actions,
                    on_step=on_step,
                )

                # Authoritative total from the DOM, exactly as build_cart does.
                total_cents = await self._guard(
                    "build_cart", "reading the cart total",
                    page.eval_on_selector(
                        "#cart-total", "(el) => parseInt(el.dataset.totalCents || '0', 10)"
                    ),
                    url=merchant_url,
                )
                products = await self._guard(
                    "build_cart", "reading the product catalog",
                    page.eval_on_selector_all(
                        "[data-product-id]",
                        """(nodes) => nodes.map((n) => ({
                            id: n.dataset.productId,
                            price_cents: parseInt(n.dataset.priceCents || '0', 10),
                            name: (n.querySelector('[data-name]') || {}).textContent || ''
                        }))""",
                    ),
                    url=merchant_url,
                )
                id_to_prod = {p["id"]: p for p in products}
                items: list[CartItem] = []
                for pid, qty in plan:
                    if qty <= 0 or pid not in id_to_prod:
                        continue
                    p = id_to_prod[pid]
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

    async def shop_live_handoff(
        self,
        merchant_url: str,
        intent: str,
        context: PurchaseContext,
        *,
        decide,
        wait_for_human,
        on_frame=None,
        on_live_view=None,
        handoff_budget_s: float = 480.0,
    ) -> OrderResult:
        """Agent shops in ONE real browser, then hands the live view to the human
        to log in / pay THEMSELVES. The agent never enters a card on this path.

        Flow, all in a single Cloudflare session (Live View does not exist for a
        local Chromium, so this refuses a local target):
          1. Agent runs the agentic loop to fill the cart (observe/add/remove).
          2. `Cloudflare.getLiveView(mode="tab")` → an INTERACTIVE URL; emitted via
             on_live_view so the UI can iframe/link it.
          3. `Cloudflare.handoff` opens the human takeover; we await EITHER the
             `Cloudflare.handoffComplete` event (human clicked Done/Failed in the
             live view) OR the app's own `wait_for_human` signal (the "Done
             paying" button in our chat), whichever lands first.
          4. While waiting, a keep-alive ping fires under Cloudflare's ≤10-min
             inactivity cap so the session cannot idle out mid-payment.
          5. Read the confirmation off the SAME page and return it.

        Doc-verified: getLiveView/handoff/handoffComplete, mode="tab", keep_alive
        ≤600000ms. See docs/superpowers/specs/2026-08-03-live-view-handoff-design.md
        and https://developers.cloudflare.com/browser-run/features/human-in-the-loop/
        """
        mode = self._mode_for(merchant_url)
        if mode != "cloudflare":
            # No Cloudflare session ⇒ no Live View to hand off. Say so plainly
            # rather than silently falling back to a path that enters a card.
            raise ShopperError(
                "shop_live",
                "Live handoff needs the Cloudflare browser; this target resolves "
                "to the local browser, which has no live view to hand off.",
                url=merchant_url,
            )

        async def note(step: str, detail: str) -> None:
            if on_frame is not None:
                await on_frame(step, detail, None)

        from app.orchestrator.agentic_shop import Product, run_agentic_shop

        async with self._page(merchant_url) as (page, _mode):
            await self._goto_ready(page, merchant_url, "[data-product-id]", "shop_live")

            # 1. Fill the cart with the agent, streaming screenshots as it goes.
            surface = _PlaywrightShopSurface(page, self, merchant_url)

            async def on_step(step: str, detail: str) -> None:
                if on_frame is None:
                    return
                shot: bytes | None = None
                with contextlib.suppress(Exception):
                    shot = await asyncio.wait_for(
                        page.screenshot(type="jpeg", quality=45), timeout=8.0
                    )
                await on_frame(step, detail, shot)

            await run_agentic_shop(
                surface,
                intent=intent,
                budget_cents=context.budget_cents,
                rules=context.rules,
                decide=decide,
                on_step=on_step,
            )

            # 2. Interactive live-view URL for the human.
            cdp = await page.context.new_cdp_session(page)
            live = await cdp.send("Cloudflare.getLiveView", {"mode": "tab", "expiresInMs": 600000})
            live_url = live.get("devtoolsFrontendUrl") if isinstance(live, dict) else None
            if not live_url:
                raise ShopperError(
                    "shop_live", "Cloudflare did not return a live-view URL.", url=merchant_url
                )
            if on_live_view is not None:
                await on_live_view(live_url)
            await note("handoff.live_view", "Live browser ready — log in and pay here.")

            # 3. Open the structured handoff and wait for whichever "done" lands
            #    first: Cloudflare's own handoffComplete (Done/Failed in the live
            #    view) or our app's wait_for_human (the chat "Done paying" button).
            loop = asyncio.get_event_loop()
            cf_done: asyncio.Future = loop.create_future()

            def _on_complete(payload: object) -> None:
                if not cf_done.done():
                    cf_done.set_result(payload if isinstance(payload, dict) else {})

            cdp.on("Cloudflare.handoffComplete", _on_complete)
            with contextlib.suppress(Exception):
                await cdp.send(
                    "Cloudflare.handoff",
                    {
                        "instructions": "Log in if needed and complete the purchase. "
                        "Click Done when the order is placed.",
                        "timeout": int(min(handoff_budget_s, 1800) * 1000),
                    },
                )

            human_task = asyncio.ensure_future(wait_for_human())
            cf_task = asyncio.ensure_future(cf_done)

            # Keep-alive: any CDP command resets the ≤10-min inactivity timer, so a
            # human typing a card (no traffic of ours) can't idle the session out.
            async def keepalive() -> None:
                try:
                    while True:
                        await asyncio.sleep(30)
                        with contextlib.suppress(Exception):
                            await cdp.send("Cloudflare.getHandoffState", {})
                except asyncio.CancelledError:
                    return

            ping = asyncio.ensure_future(keepalive())
            cancelled_human = False
            try:
                done, _pending = await asyncio.wait(
                    {human_task, cf_task},
                    timeout=handoff_budget_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    raise ShopperError(
                        "shop_live",
                        "The checkout window closed before it was finished — "
                        "nothing was completed. Start again when you're ready.",
                        url=merchant_url,
                    )
                # If OUR button resolved and said cancel, stop honestly.
                if human_task in done:
                    verdict = human_task.result()
                    if isinstance(verdict, dict) and verdict.get("approved") is False:
                        cancelled_human = True
            finally:
                ping.cancel()
                for t in (human_task, cf_task):
                    if not t.done():
                        t.cancel()
                with contextlib.suppress(Exception):
                    await cdp.detach()

            if cancelled_human:
                raise ShopperError(
                    "shop_live", "You cancelled the checkout; nothing was placed.",
                    url=merchant_url,
                )

            # 4. Read the confirmation off the SAME page. The merchant's own
            #    success page is unknown DOM, so this is best-effort: look for an
            #    ORD-style id / an order-number pattern in the visible text.
            confirmation_text = ""
            with contextlib.suppress(Exception):
                confirmation_text = (
                    await asyncio.wait_for(page.inner_text("body"), timeout=8.0)
                ).strip()

        order_id = ""
        m = re.search(r"\b(?:ORD-?\d+|order\s*#?\s*[A-Za-z0-9-]{4,})\b", confirmation_text, re.I)
        if m:
            order_id = m.group(0)
        return OrderResult(
            order_id=order_id or "placed",
            confirmation_text=confirmation_text[:2000] or "Checkout handed off to you.",
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


class _PlaywrightShopSurface:
    """ShopSurface (app.orchestrator.agentic_shop) over the real storefront DOM.

    Reads the shelf and the cart straight off the page, and drives the same
    add/remove buttons a human would — button[data-add=<id>] /
    button[data-remove=<id>], with the cart re-read from #cart-lines each time so
    the loop's view can never drift from the store's own state.
    """

    def __init__(self, page: Page, broker: "CloudflareShopperBroker", url: str) -> None:
        self._page = page
        self._broker = broker
        self._url = url

    async def catalog(self):
        from app.orchestrator.agentic_shop import Product

        rows = await self._broker._guard(
            "build_cart", "reading the product catalog",
            self._page.eval_on_selector_all(
                "[data-product-id]",
                """(nodes) => nodes.map((n) => ({
                    id: n.dataset.productId,
                    brand: n.dataset.brand || '',
                    price_cents: parseInt(n.dataset.priceCents || '0', 10),
                    name: (n.querySelector('[data-name]') || {}).textContent || ''
                }))""",
            ),
            url=self._url,
        )
        return [
            Product(
                id=r["id"],
                name=(r["name"] or "").strip(),
                brand=r["brand"] or "",
                price_cents=int(r["price_cents"]),
            )
            for r in rows
            if r.get("id")
        ]

    async def cart(self) -> dict[str, int]:
        rows = await self._broker._guard(
            "build_cart", "reading the cart",
            self._page.eval_on_selector_all(
                "#cart-lines li[data-line]",
                """(nodes) => nodes.map((n) => ({
                    id: n.dataset.line,
                    qty: parseInt(n.dataset.qty || '0', 10)
                }))""",
            ),
            url=self._url,
        )
        return {r["id"]: int(r["qty"]) for r in rows if r.get("id") and int(r["qty"]) > 0}

    async def add(self, product_id: str) -> None:
        await self._broker._guard(
            "build_cart", f"adding {product_id} to cart",
            self._page.click(
                f'button[data-add="{product_id}"]', timeout=self._broker._action_timeout_ms
            ),
            url=self._url,
        )

    async def remove(self, product_id: str) -> None:
        await self._broker._guard(
            "build_cart", f"removing {product_id} from cart",
            self._page.click(
                f'button[data-remove="{product_id}"]', timeout=self._broker._action_timeout_ms
            ),
            url=self._url,
        )


# ── selection helpers ──────────────────────────────────────────────────────────
# Moved to app.brokers.policy so the Prava wallet shopper applies the SAME policy
# reading without importing Playwright. Re-exported here because these names are
# part of this module's surface for the existing tests and scripts.

from app.brokers.policy import (  # noqa: E402  (kept at the bottom, next to what it replaced)
    _is_disallowed,
    _preferred_brands,
    _select_items,
)

__all__ = [
    "CloudflareShopperBroker",
    "ShopperError",
    "_checkout_url_for",
    "_is_disallowed",
    "_preferred_brands",
    "_select_items",
]
