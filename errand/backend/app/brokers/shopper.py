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

import contextlib
import os
import re
from typing import AsyncIterator, Literal
from urllib.parse import urlparse, urlsplit, urlunsplit, parse_qsl, urlencode

from playwright.async_api import Page, async_playwright

from app.contracts import (
    CartItem,
    CartResult,
    CheckoutState,
    OrderResult,
    PaymentCredential,
    PurchaseContext,
)

BrowserMode = Literal["cloudflare", "local"]


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
                    ws, headers={"Authorization": f"Bearer {self._api_token}"}
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

    # ── ShopperBroker Protocol ────────────────────────────────────────────────

    async def build_cart(
        self, merchant_url: str, intent: str, context: PurchaseContext
    ) -> CartResult:
        """Navigate the store, add items honoring budget + brand rules, read the
        real DOM total, and return a resumable CheckoutState."""
        preferred = _preferred_brands(context.rules)

        async with self._page(merchant_url) as (page, _mode):
            await page.goto(merchant_url, wait_until="networkidle")
            await page.wait_for_selector("[data-product-id]")

            products = await page.eval_on_selector_all(
                "[data-product-id]",
                """(nodes) => nodes.map((n) => ({
                    id: n.dataset.productId,
                    brand: n.dataset.brand || '',
                    price_cents: parseInt(n.dataset.priceCents || '0', 10),
                    name: (n.querySelector('[data-name]') || {}).textContent || ''
                }))""",
            )

            plan = _select_items(
                products, context.budget_cents, preferred, context.rules
            )

            # Actually click the add-to-cart buttons — real browser interaction.
            for prod_id, qty in plan:
                for _ in range(qty):
                    await page.click(f'button[data-add="{prod_id}"]')

            # Read the authoritative total straight from the DOM.
            total_cents = await page.eval_on_selector(
                "#cart-total", "(el) => parseInt(el.dataset.totalCents || '0', 10)"
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

        async with self._page(checkout_url) as (page, _mode):
            await page.goto(checkout_url, wait_until="networkidle")
            await page.wait_for_selector("#checkout-form")

            # Type the one-time Prava card credential into the real form fields.
            await page.fill("#card-number", credential.token)
            await page.fill("#expiry-month", credential.expiry_month)
            await page.fill("#expiry-year", credential.expiry_year)
            await page.fill("#cvv", credential.dynamic_cvv)

            await page.click("#place-order")

            # Wait for the confirmation to render, then scrape it.
            await page.wait_for_selector("#confirmation.show", timeout=15000)
            order_id = await page.eval_on_selector(
                "#confirmation", "(el) => el.dataset.orderId || ''"
            )
            confirmation_text = (
                await page.eval_on_selector(
                    "#confirmation", "(el) => el.innerText"
                )
            ).strip()

        if not order_id:
            # fall back to parsing the id out of the confirmation copy
            m = re.search(r"ORD-\d+", confirmation_text)
            order_id = m.group(0) if m else ""

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
