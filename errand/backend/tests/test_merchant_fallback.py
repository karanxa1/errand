"""What happens when the approved merchant doesn't stock it.

An approved vendor being out of stock is ordinary, so the orchestrator walks a
ladder: every vendor the policy approved, then — only if the profile allows it —
whoever else in Prava's catalog has the item. Two invariants make that safe
rather than reckless, and both are asserted here:

  1. The card follows the cart. `run_errand` mints a Prava session scoped to a
     merchant, and that must be the merchant that actually filled the cart, not
     the first name on the policy list. Getting this wrong produces a decline
     that only shows up AFTER the human has approved the spend — which is why
     there is also a mismatch guard, tested below.
  2. Nothing widens silently. Every rung emits an audit event, and buying from a
     vendor the policy never named emits a loud one.

No network: the shopper, payment and mail brokers are all stubs.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.brokers import Brokers  # noqa: E402
from app.config import settings  # noqa: E402
from app.contracts import (  # noqa: E402
    CartItem,
    CartResult,
    CheckoutState,
    CreateSessionResult,
    InboxMessage,  # noqa: F401  (imported for the mail stub's typing story)
    Merchant,
    OrderConfirmation,
    PaymentCredential,
    PollCompleted,
    PurchaseContext,
)
from app.orchestrator.guards import ApprovalDecision  # noqa: E402
from app.orchestrator.run_errand import run_errand  # noqa: E402


class _Shopper:
    """Stocks only the merchants named in `stocked`; knows about `catalog`."""

    def __init__(self, stocked: dict[str, int], catalog: list[str] | None = None) -> None:
        self.stocked = stocked
        self.catalog = catalog
        self.asked: list[str] = []
        self.discovery_calls = 0

    async def build_cart(self, merchant_url: str, intent: str, context: PurchaseContext) -> CartResult:
        self.asked.append(merchant_url)
        host = merchant_url.split("://")[-1].split("/")[0]
        if host not in self.stocked:
            raise RuntimeError(f"no matching product at {host}")
        price = self.stocked[host]
        items = [CartItem(name="Coffee", qty=1, price_cents=price)]
        return CartResult(
            items=items,
            total_cents=price,
            checkout=CheckoutState(
                merchant_url=merchant_url, items=items, session_ref=f"cs_{host}"
            ),
        )

    async def complete_checkout(self, checkout: CheckoutState, credential: PaymentCredential):
        from app.contracts import OrderResult

        return OrderResult(order_id="ord_1", confirmation_text="Paid")

    async def discover_merchants(self, intent, context, limit):
        self.discovery_calls += 1
        if self.catalog is None:
            raise AssertionError("discovery must not be called when it is disallowed")
        return [Merchant(name=d, url=f"https://{d}") for d in self.catalog[:limit]]


class _NoCatalogShopper(_Shopper):
    """A shopper with a single storefront and no wider world (no discovery)."""

    discover_merchants = None  # type: ignore[assignment]


class _Payment:
    def __init__(self) -> None:
        self.scoped_to: str | None = None
        self.browser_profile_id: str | None = None
        self.customer_email: str | None = None

    async def create_session(self, data) -> CreateSessionResult:
        self.scoped_to = data.merchant.url
        self.browser_profile_id = data.browser_profile_id
        self.customer_email = data.user_email
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
    def __init__(self, merchants: list[str], budget_cents: int = 20000) -> None:
        self.merchants = merchants
        self.budget_cents = budget_cents

    async def get_context(self, profile, intent) -> PurchaseContext:
        return PurchaseContext(
            profile=profile,
            approved_merchants=[Merchant(name=d, url=f"https://{d}") for d in self.merchants],
            budget_cents=self.budget_cents,
            rules=[],
        )


def _run(shopper, context, *, profile="business", discovery="personal", bpid=None):
    """Run one errand to completion, returning (result, events, payment)."""
    payment = _Payment()
    brokers = Brokers(context=context, shopper=shopper, payment=payment, mail=_Mail())
    events: list = []

    async def emit(event) -> None:
        events.append(event)

    async def approve(_payload) -> ApprovalDecision:
        return ApprovalDecision(approved=True, approval_id="ap_1")

    original = settings.merchant_discovery
    settings.merchant_discovery = discovery
    try:
        result = asyncio.run(
            run_errand(
                brokers,
                profile=profile,
                intent="buy dark roast coffee",
                user_id="u1",
                user_email_fallback="buyer@example.com",
                browser_profile_id=bpid,
                emit=emit,
                approve=approve,
            )
        )
    finally:
        settings.merchant_discovery = original
    return result, events, payment


def _steps(events) -> list[str]:
    return [e.step for e in events]


def test_the_second_approved_vendor_is_tried_when_the_first_is_empty() -> None:
    """approved_merchants is a LIST and only [0] was ever used.

    A policy that names three vendors meant two of them were decoration: one
    empty shelf aborted the whole errand.
    """
    shopper = _Shopper({"second.test": 1500})
    result, events, payment = _run(shopper, _Context(["first.test", "second.test"]))
    assert result["kind"] == "completed", result
    assert shopper.asked == ["https://first.test", "https://second.test"]
    assert "cart.merchant_unavailable" in _steps(events)
    # And the card follows the cart, not the policy's first entry.
    assert payment.scoped_to == "https://second.test"


def test_the_card_is_scoped_to_the_merchant_that_filled_the_cart() -> None:
    """The bug this whole feature would otherwise have shipped.

    Scoping to approved_merchants[0] while shopping somewhere else produces a
    network decline that lands only after the human approved the spend.
    """
    shopper = _Shopper({"third.test": 900})
    _, _, payment = _run(shopper, _Context(["first.test", "second.test", "third.test"]))
    assert payment.scoped_to == "https://third.test"


def test_business_profile_does_not_wander_off_the_approved_list() -> None:
    """"Avoid non-approved vendors" is a rule, not a suggestion to drop when a
    shelf is empty. Default policy: discovery is personal-profile only."""
    shopper = _Shopper({"elsewhere.test": 500}, catalog=None)
    result, events, payment = _run(shopper, _Context(["first.test"]), profile="business")
    assert result["kind"] == "aborted"
    assert shopper.discovery_calls == 0
    assert payment.scoped_to is None  # no card was ever minted
    assert "cart.unavailable" in _steps(events)


def test_personal_profile_falls_back_to_the_catalog_and_says_so_loudly() -> None:
    shopper = _Shopper({"elsewhere.test": 1200}, catalog=["elsewhere.test"])
    result, events, payment = _run(shopper, _Context(["first.test"]), profile="personal")
    assert result["kind"] == "completed", result
    assert shopper.discovery_calls == 1
    assert payment.scoped_to == "https://elsewhere.test"

    discovered = [e for e in events if e.step == "cart.merchant_discovered"]
    assert len(discovered) == 1
    # The operator must be able to read WHERE and WHY off the audit trail alone.
    assert "elsewhere.test" in discovered[0].detail
    assert "policy did not name" in discovered[0].detail
    # And it happens BEFORE the approval gate, so the gate can show it.
    steps = _steps(events)
    assert steps.index("cart.merchant_discovered") < steps.index("approval.granted")


def test_discovery_can_be_switched_off_entirely() -> None:
    shopper = _Shopper({"elsewhere.test": 1200}, catalog=None)
    result, _, _ = _run(shopper, _Context(["first.test"]), profile="personal", discovery="off")
    assert result["kind"] == "aborted"
    assert shopper.discovery_calls == 0


def test_always_lets_a_business_errand_use_the_catalog() -> None:
    shopper = _Shopper({"elsewhere.test": 1200}, catalog=["elsewhere.test"])
    result, _, payment = _run(
        shopper, _Context(["first.test"]), profile="business", discovery="always"
    )
    assert result["kind"] == "completed", result
    assert payment.scoped_to == "https://elsewhere.test"


def test_a_shopper_with_no_catalog_just_has_no_last_resort() -> None:
    """The storefront and mock shoppers have one shop; that must not crash."""
    shopper = _NoCatalogShopper({"other.test": 100})
    result, _, _ = _run(shopper, _Context(["first.test"]), profile="personal")
    assert result["kind"] == "aborted"


def test_attempts_are_capped_so_a_long_vendor_list_cannot_run_forever() -> None:
    """Each wallet attempt spins a real browser for the quote (20-40s)."""
    shopper = _Shopper({})
    original = settings.max_merchant_attempts
    settings.max_merchant_attempts = 2
    try:
        _run(shopper, _Context(["a.test", "b.test", "c.test", "d.test"]))
    finally:
        settings.max_merchant_attempts = original
    assert len(shopper.asked) == 2


def test_a_cart_parked_at_the_wrong_merchant_is_refused_before_any_card() -> None:
    """The guard for a shopper that returns a cart from somewhere else."""

    class _Liar(_Shopper):
        async def build_cart(self, merchant_url, intent, context) -> CartResult:
            items = [CartItem(name="Coffee", qty=1, price_cents=1000)]
            return CartResult(
                items=items,
                total_cents=1000,
                # Parked somewhere other than what was asked for.
                checkout=CheckoutState(
                    merchant_url="https://somewhere-else.test", items=items, session_ref="cs_x"
                ),
            )

    result, events, payment = _run(_Liar({}), _Context(["first.test"]))
    assert result["kind"] == "aborted"
    assert "cart.merchant_mismatch" in _steps(events)
    assert payment.scoped_to is None  # refused before minting anything


def test_the_browser_profile_id_reaches_the_payment_session() -> None:
    """The whole point of persisting it client-side is that it arrives here.

    It crosses four boundaries — HTTP request model, run_errand,
    CreateSessionInput, the Prava body — and a break at any one of them is
    silent: pydantic ignores unknown fields, so the value is simply dropped and
    every checkout goes back to looking like a brand-new device.
    """
    shopper = _Shopper({"first.test": 1000})
    _, _, payment = _run(shopper, _Context(["first.test"]), bpid="bp_persisted_42")
    assert payment.browser_profile_id == "bp_persisted_42"


def test_prava_gets_the_humans_email_not_the_agent_mailbox() -> None:
    """Two different jobs that were conflated.

    The agent inbox catches the MERCHANT's order confirmation. Prava's
    `user_email` is the CUSTOMER identity, forwarded to the card network for
    passkey registration. Sending the inbox registered a person's passkey
    against an address they do not own and cannot read.
    """
    shopper = _Shopper({"first.test": 1000})
    _, _, payment = _run(shopper, _Context(["first.test"]))
    assert payment.customer_email == "buyer@example.com"
    assert "agentmail" not in (payment.customer_email or "")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{'FAILED' if failures else 'PASSED'} — {failures} failure(s)")
    sys.exit(1 if failures else 0)
