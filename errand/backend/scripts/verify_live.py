"""Verify the real brokers against LIVE sandboxes (no mocks).

Run: cd backend && uv run python -m scripts.verify_live
Proves: Senso returns cited context for both profiles; Prava creates a real
session. Exits non-zero on failure.
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.brokers.prava import PravaPaymentBroker
from app.brokers.senso import SensoContextBroker
from app.contracts import CartItem, CreateSessionInput, Merchant


async def main() -> None:
    print("=== Senso (live) ===")
    senso = SensoContextBroker(settings.senso_api_key, settings.senso_api_base)
    biz = await senso.get_context("business", "restock the office pantry")
    per = await senso.get_context("personal", "order my weekly groceries")
    print(f"  business: budget=${biz.budget_cents/100:.2f} merchant={biz.approved_merchants[0].name!r} "
          f"rules={len(biz.rules)} citations={len(biz.citations)}")
    print(f"  personal: budget=${per.budget_cents/100:.2f} rules={len(per.rules)} citations={len(per.citations)}")
    assert biz.budget_cents == 20000, f"expected business $200, got {biz.budget_cents}"
    assert per.budget_cents == 6000, f"expected personal $60, got {per.budget_cents}"
    assert biz.citations and per.citations, "citations must be present"

    print("=== Prava (live sandbox) ===")
    prava = PravaPaymentBroker(settings.prava_secret_key, settings.prava_api_base)
    session = await prava.create_session(
        CreateSessionInput(
            merchant=Merchant(name="Demo Pantry Co", url="https://demo-pantry.example.com"),
            total_cents=6300,
            user_id="u_verify",
            user_email="agent@demo.agentmail.to",
            items=[CartItem(name="Pantry restock", qty=1, price_cents=6300)],
        )
    )
    print(f"  session_id={session.session_id}")
    print(f"  iframe_url={session.iframe_url[:60]}...")
    assert session.session_id, "no session id"
    assert session.iframe_url.startswith("http"), "no iframe url"

    print("\n✅ Live verification passed: Senso + Prava real brokers work.")


if __name__ == "__main__":
    asyncio.run(main())
