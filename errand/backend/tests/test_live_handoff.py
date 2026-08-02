"""Live browser handoff — the safe-gating and refusal contract.

The full handoff (getLiveView → human pays → read confirmation) needs a real
Cloudflare session and a human, so it cannot run in CI. What CAN and MUST be
pinned is the SAFETY ENVELOPE around it:

  * the tool/capability is OFF unless it is both enabled AND the Cloudflare
    browser it requires is configured (Live View has no local equivalent);
  * a live handoff against a LOCAL target is refused, loudly, rather than
    silently falling back to a path that would enter a card.

Runs under pytest, and standalone (`uv run python tests/test_live_handoff.py`).
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import run_standalone  # noqa: E402

from app.brokers.shopper import CloudflareShopperBroker, ShopperError  # noqa: E402
from app.config import Settings  # noqa: E402
from app.contracts import PurchaseContext  # noqa: E402


def test_live_handoff_ready_requires_flag_and_cloudflare_creds() -> None:
    """The model is only offered shop_live when the deployment can perform it.
    Missing the flag OR the Cloudflare creds → not ready."""
    off = Settings(use_live_handoff=False, cloudflare_account_id="a", cloudflare_api_token="t")
    assert off.live_handoff_ready is False, "flag off must be not-ready"

    no_creds = Settings(use_live_handoff=True, cloudflare_account_id="", cloudflare_api_token="")
    assert no_creds.live_handoff_ready is False, "no Cloudflare creds must be not-ready"

    ready = Settings(use_live_handoff=True, cloudflare_account_id="acct", cloudflare_api_token="tok")
    assert ready.live_handoff_ready is True, "flag on + creds present must be ready"


def test_live_handoff_refuses_a_local_target() -> None:
    """Cloudflare Live View has no local-browser equivalent, so a localhost/file
    target must be refused — never silently downgraded to a card-entering path."""
    broker = CloudflareShopperBroker(force_local=True)  # forces the local path
    ctx = PurchaseContext(profile="personal", approved_merchants=[], budget_cents=10**9, rules=[])

    async def _human():  # never reached
        return {"approved": True}

    async def _decide(**_kwargs):
        return {"action": "done"}

    try:
        asyncio.run(
            broker.shop_live_handoff(
                "http://localhost:3000/store", "buy socks", ctx,
                decide=_decide, wait_for_human=_human,
            )
        )
    except ShopperError as exc:
        assert exc.step == "shop_live"
        assert "live view" in str(exc).lower() or "local" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("a local target must be refused, not handed off")


if __name__ == "__main__":
    sys.exit(1 if run_standalone(dict(globals())) else 0)
