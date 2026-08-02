"""A user spend cap must actually reach the shopper and shrink the cart.

The bug this pins: `run_errand` built the cart to the full policy budget and had
no way to take a spoken limit. A user watching a $59.60 cart and saying "make it
under $10" changed nothing — the model re-called the same tool, the shopper read
`context.budget_cents` (the policy), and handed back the identical cart. The fix
threads an optional `max_cents` into a COPY of the shopping context as
min(policy, cap), so the shopper fills to the cap instead, while the real policy
budget stays the ceiling and the over-budget backstop still uses it.

No network: the shopper, payment and mail brokers are stubs. The shopper here is
deliberately budget-AWARE (unlike the fallback test's fixed-price stub) so the
cap has something to bite on.

Runs under pytest, and standalone (`uv run python tests/test_spend_cap.py`).
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import run_standalone  # noqa: E402

from app.brokers import Brokers  # noqa: E402
from app.config import settings  # noqa: E402
from app.contracts import (  # noqa: E402
    CartItem,
    CartResult,
    CheckoutState,
    CreateSessionResult,
    Merchant,
    OrderConfirmation,
    OrderResult,
    PaymentCredential,
    PollCompleted,
    PurchaseContext,
)
from app.orchestrator.guards import ApprovalDecision  # noqa: E402
from app.orchestrator.run_errand import run_errand  # noqa: E402


class _BudgetAwareShopper:
    """Fills the cart with as many $6.00 units as the budget it is GIVEN allows.

    This is the crux: it reads `context.budget_cents`, exactly as the real
    storefront shopper does via `_select_items`. So whatever budget the cap logic
    puts on the shopping context is what shapes the cart — which is what the test
    asserts. Records the budget it saw so the test can prove the cap arrived.
    """

    UNIT_CENTS = 600

    def __init__(self) -> None:
        self.seen_budget_cents: int | None = None

    async def build_cart(
        self, merchant_url: str, intent: str, context: PurchaseContext
    ) -> CartResult:
        self.seen_budget_cents = context.budget_cents
        qty = context.budget_cents // self.UNIT_CENTS
        items = (
            [CartItem(name="Snack bar", qty=qty, price_cents=self.UNIT_CENTS)]
            if qty > 0
            else []
        )
        total = qty * self.UNIT_CENTS
        return CartResult(
            items=items,
            total_cents=total,
            checkout=CheckoutState(
                merchant_url=merchant_url, items=items, session_ref="cs_x"
            ),
        )

    async def complete_checkout(self, checkout, credential) -> OrderResult:
        return OrderResult(order_id="ord_1", confirmation_text="Paid")


class _Payment:
    def __init__(self) -> None:
        self.total_cents: int | None = None

    async def create_session(self, data) -> CreateSessionResult:
        self.total_cents = data.total_cents
        return CreateSessionResult(session_id="ses_1", iframe_url="https://collect.example.com")

    async def poll_credential(self, session_id: str):
        return PollCompleted(
            credential=PaymentCredential(
                token="4111111111111111",
                dynamic_cvv="123",
                expiry_month="12",
                expiry_year="2030",
                txn_ref_id="tli_1",
            )
        )

    async def report_status(self, session_id, txn_ref_id, status) -> None:
        return None


class _Mail:
    async def ensure_inbox(self) -> str:
        return "agent-inbox@agentmail.to"

    async def wait_for_confirmation(self, merchant, since_iso, timeout_ms) -> OrderConfirmation:
        return OrderConfirmation(matched=False)

    async def list_messages(self, limit: int = 10):
        return []

    async def reply(self, message_id: str, text: str) -> None:
        return None


class _Context:
    def __init__(self, budget_cents: int) -> None:
        self.budget_cents = budget_cents

    async def get_context(self, profile, intent) -> PurchaseContext:
        return PurchaseContext(
            profile=profile,
            approved_merchants=[Merchant(name="Shop", url="https://shop.test")],
            budget_cents=self.budget_cents,
            rules=[],
        )


def _run(shopper, context, *, max_cents=None):
    payment = _Payment()
    brokers = Brokers(context=context, shopper=shopper, payment=payment, mail=_Mail())
    events: list = []

    async def emit(event) -> None:
        events.append(event)

    async def approve(_payload) -> ApprovalDecision:
        return ApprovalDecision(approved=True, approval_id="ap_1")

    # Keep discovery off so the single stubbed merchant is the whole world.
    original = settings.merchant_discovery
    settings.merchant_discovery = "never"
    try:
        result = asyncio.run(
            run_errand(
                brokers,
                profile="business",
                intent="order some snacks",
                user_id="u1",
                user_email_fallback="buyer@example.com",
                emit=emit,
                approve=approve,
                max_cents=max_cents,
            )
        )
    finally:
        settings.merchant_discovery = original
    return result, events, payment


def _steps(events) -> list[str]:
    return [e.step for e in events]


def test_no_cap_fills_to_the_policy_budget() -> None:
    """Baseline: with no cap the shopper sees the full policy budget — the
    behaviour that produced the same large cart every time. $60 → 10 units."""
    shopper = _BudgetAwareShopper()
    result, _events, payment = _run(shopper, _Context(6000))
    assert shopper.seen_budget_cents == 6000
    assert result["kind"] == "completed", result
    assert payment.total_cents == 6000  # 10 × $6.00


def test_a_user_cap_shrinks_the_cart() -> None:
    """The fix: 'under $10' on a $60 policy must build a ~$6 cart, not a $60 one.
    The shopper must SEE 1000, not 6000, and the charge must land at/under it."""
    shopper = _BudgetAwareShopper()
    result, events, payment = _run(shopper, _Context(6000), max_cents=1000)
    assert shopper.seen_budget_cents == 1000, "the cap never reached the shopper"
    assert "cart.capped" in _steps(events)
    assert result["kind"] == "completed", result
    assert payment.total_cents is not None and payment.total_cents <= 1000


def test_a_cap_above_policy_is_ignored() -> None:
    """The policy is the ceiling: a cap ABOVE it changes nothing, and the shopper
    still sees the policy budget — the cap can only ever tighten spend."""
    shopper = _BudgetAwareShopper()
    _run(shopper, _Context(6000), max_cents=999999)
    assert shopper.seen_budget_cents == 6000


def test_a_cap_below_the_cheapest_item_aborts_without_a_session() -> None:
    """A cap nothing fits under must NOT pin a $0 card — it aborts with a clear
    'nothing fit' reason, and no payment session is ever created."""
    shopper = _BudgetAwareShopper()  # cheapest unit is $6.00
    result, events, payment = _run(shopper, _Context(6000), max_cents=300)
    assert result["kind"] == "aborted", result
    assert "cart.capped_empty" in _steps(events)
    assert payment.total_cents is None, "no session may be created for an empty cart"


if __name__ == "__main__":
    sys.exit(1 if run_standalone(dict(globals())) else 0)
