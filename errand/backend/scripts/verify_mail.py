"""Verify the AgentMail MailBroker against the LIVE AgentMail API (no mocks).

Run:
  cd backend && UV_CACHE_DIR=... uv run python -m scripts.verify_mail

Proves: a real inbox is created and its address printed; the inbox lists
messages; wait_for_confirmation polls and returns cleanly (matched likely False
with no email — that's fine, the point is the loop runs and returns).
Exits non-zero on failure.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import settings
from app.brokers.mail import AgentMailBroker


async def main() -> None:
    print("=== AgentMail (live) ===")
    mail = AgentMailBroker(settings.agentmail_api_key)

    address = await mail.ensure_inbox()
    print(f"  inbox address: {address}")

    msgs = await mail.list_messages(5)
    print(f"  messages listed: {len(msgs)}")

    now_iso = datetime.now(timezone.utc).isoformat()
    result = await mail.wait_for_confirmation(
        "https://demo-pantry.example.com", now_iso, timeout_ms=6000
    )
    print(f"  wait_for_confirmation matched: {result.matched}")
    if result.matched:
        print(f"    order_id={result.order_id} total_cents={result.total_cents}")

    assert "@" in address, f"inbox address is not an email: {address!r}"

    print("\n✅ Live verification passed: AgentMail real broker works.")


if __name__ == "__main__":
    asyncio.run(main())
