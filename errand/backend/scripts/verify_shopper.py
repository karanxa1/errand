"""Verify the ShopperBroker against a REAL browser on a controlled demo store.

What this proves:
  1. (best effort) Cloudflare Browser Rendering is reachable over CDP with the
     credentials in .env — connects, opens a public page, reads its title.
  2. The shopper drives a REAL browser through the demo storefront: it adds items
     honoring the budget + brand rules, reads the real cart total from the DOM,
     then types a Prava card credential into the checkout form, places the order,
     and scrapes a real order confirmation.

The demo store is served locally over http.server on a free port, so the browser
navigates real http:// URLs (not file://). Because Cloudflare's remote browser
cannot reach localhost, the shopping flow runs on LOCAL Playwright Chromium — the
same automation code Cloudflare would run for a public merchant.

Run:
    cd backend && UV_CACHE_DIR=$PWD/.uvcache uv run python -m scripts.verify_shopper

Exits non-zero on any failure.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import socket
import socketserver
import threading
from pathlib import Path

from app.brokers.shopper import CloudflareShopperBroker, _CF_WS_TEMPLATE
from app.config import settings
from app.contracts import (
    Citation,
    Merchant,
    PaymentCredential,
    PurchaseContext,
)

_STORE_DIR = Path(__file__).resolve().parents[1] / "app" / "demo_store"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve_store(port: int) -> socketserver.TCPServer:
    """Start a background static server rooted at the demo store dir."""
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(_STORE_DIR)
    )

    class _Quiet(socketserver.TCPServer):
        allow_reuse_address = True

        def handle_error(self, request, client_address):  # noqa: D401
            pass  # keep the verify output clean

    httpd = _Quiet(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


async def _cloudflare_smoke_test() -> str:
    """Best-effort proof that Cloudflare Browser Rendering works over CDP.

    Returns a human-readable status line. Never raises — a failure here does not
    fail the run, because the demo store must use local Playwright regardless
    (Cloudflare cannot reach localhost). This just reports reachability.
    """
    account = settings.cloudflare_account_id
    token = settings.cloudflare_api_token
    if not (account and token):
        return "SKIPPED — no CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN in env"

    from playwright.async_api import async_playwright

    ws = _CF_WS_TEMPLATE.format(account_id=account)
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(
                ws, headers={"Authorization": f"Bearer {token}"}, timeout=30000
            )
            try:
                ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto("https://example.com", wait_until="domcontentloaded", timeout=30000)
                title = await page.title()
                return f"OK — connected, example.com title={title!r}"
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE — {type(exc).__name__}: {exc}"


async def main() -> None:
    print("=== Cloudflare Browser Rendering (CDP) smoke test ===")
    cf_status = await _cloudflare_smoke_test()
    print(f"  endpoint: {_CF_WS_TEMPLATE.format(account_id=settings.cloudflare_account_id or '<ACCOUNT_ID>')}")
    print(f"  status:   {cf_status}")

    port = _free_port()
    httpd = _serve_store(port)
    store_url = f"http://127.0.0.1:{port}/index.html"
    print(f"\n=== Demo store served at {store_url} ===")

    try:
        # Force local browser: the store is on localhost, unreachable from CF.
        broker = CloudflareShopperBroker(force_local=True)

        context = PurchaseContext(
            profile="business",
            approved_merchants=[Merchant(name="Demo Pantry Co", url=store_url)],
            budget_cents=20000,
            rules=[
                "Prefer Blue Bottle/Clif/LaCroix",
                "No energy drinks",
                "Under $200",
            ],
            citations=[
                Citation(source="Procurement Policy v3", snippet="$200 cap; approved vendors only")
            ],
        )

        print("\n=== build_cart (real browser) ===")
        cart = await broker.build_cart(store_url, "restock the office pantry", context)
        for it in cart.items:
            print(f"  - {it.name}  x{it.qty}  @ ${it.price_cents/100:.2f}")
        print(f"  TOTAL: ${cart.total_cents/100:.2f}  (budget ${context.budget_cents/100:.2f})")
        print(f"  checkout session_ref: {cart.checkout.session_ref}")

        assert cart.total_cents > 0, "cart total must be > 0"
        assert cart.total_cents <= context.budget_cents, "cart total must be within budget"
        assert cart.items, "cart must have at least one item"
        # brand rule: no energy drinks should have been added
        assert not any("energy" in it.name.lower() for it in cart.items), (
            "energy drinks must be excluded by the 'No energy drinks' rule"
        )

        print("\n=== complete_checkout (real browser types Prava credential) ===")
        credential = PaymentCredential(
            token="4111111111111111",
            dynamic_cvv="123",
            expiry_month="12",
            expiry_year="2029",
            txn_ref_id="t_verify",
        )
        order = await broker.complete_checkout(cart.checkout, credential)
        print(f"  order_id: {order.order_id}")
        print(f"  confirmation: {order.confirmation_text}")

        assert order.order_id, "order_id must be present"
        assert order.order_id.startswith("ORD-"), "order_id should look like ORD-######"
        assert order.confirmation_text, "confirmation text must be present"

        print("\n✅ Shopper verification passed: real browser built a cart and completed checkout.")
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    asyncio.run(main())
