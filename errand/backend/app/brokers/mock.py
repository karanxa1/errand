"""Mock brokers — make the whole flow runnable with zero external calls.

Real brokers (prava.py, senso.py verified; shopper + mail pending) drop in via
the registry without touching the orchestrator.
"""

from __future__ import annotations

import time

from app.contracts import (
    CartItem,
    CartResult,
    CheckoutState,
    CreateSessionInput,
    CreateSessionResult,
    InboxMessage,
    OrderConfirmation,
    OrderResult,
    PaymentCredential,
    PollCompleted,
    PollCredentialResult,
    PollPending,
    ProfileKind,
    PurchaseContext,
    Merchant,
    Citation,
    TxnStatus,
)


class MockContextBroker:
    async def get_context(self, profile: ProfileKind, intent: str) -> PurchaseContext:
        if profile == "business":
            return PurchaseContext(
                profile=profile,
                approved_merchants=[Merchant(name="Demo Pantry Co", url="https://demo-pantry.example.com")],
                budget_cents=20000,
                rules=["Prefer Blue Bottle/Clif/LaCroix", "No energy drinks", "Under $200"],
                citations=[Citation(source="Procurement Policy v3", snippet="$200 cap; approved vendors only")],
            )
        return PurchaseContext(
            profile=profile,
            approved_merchants=[Merchant(name="Demo Pantry Co", url="https://demo-pantry.example.com")],
            budget_cents=6000,
            rules=["Oat milk, dark roast, sparkling water", "Low sugar", "Under $60"],
            citations=[Citation(source="My Preferences", snippet="$60 weekly; oat milk, dark roast")],
        )


class MockShopperBroker:
    async def build_cart(self, merchant_url, intent, context) -> CartResult:
        items = [
            CartItem(name="Blue Bottle Coffee 12oz", qty=2, price_cents=1800),
            CartItem(name="Clif Bars (12 pack)", qty=1, price_cents=1500),
            CartItem(name="LaCroix Sparkling Water (24)", qty=1, price_cents=1200),
        ]
        total = sum(i.qty * i.price_cents for i in items)
        while total > context.budget_cents and items:
            items.pop()
            total = sum(i.qty * i.price_cents for i in items)
        return CartResult(
            items=items,
            total_cents=total,
            checkout=CheckoutState(merchant_url=merchant_url, items=items, session_ref=f"mock-{int(time.time())}"),
        )

    async def complete_checkout(self, checkout, credential) -> OrderResult:
        oid = f"ORD-{int(time.time()) % 1_000_000}"
        return OrderResult(order_id=oid, confirmation_text=f"Order {oid} placed at {checkout.merchant_url}")


class MockPaymentBroker:
    def __init__(self) -> None:
        self._polls: dict[str, int] = {}

    async def create_session(self, data: CreateSessionInput) -> CreateSessionResult:
        sid = f"sess_mock_{int(time.time()*1000)}"
        return CreateSessionResult(session_id=sid, iframe_url=f"https://sandbox.prava.space/iframe?session={sid}")

    async def poll_credential(self, session_id: str) -> PollCredentialResult:
        n = self._polls.get(session_id, 0) + 1
        self._polls[session_id] = n
        if n < 2:
            return PollPending()
        return PollCompleted(
            credential=PaymentCredential(
                token="4111111111111111", dynamic_cvv="123",
                expiry_month="12", expiry_year="2029", txn_ref_id=f"txn_{session_id}",
            )
        )

    async def report_status(self, session_id: str, txn_ref_id: str, status: TxnStatus) -> None:
        return None


class MockMailBroker:
    _address = "errand-agent@demo.agentmail.to"

    async def ensure_inbox(self) -> str:
        return self._address

    async def wait_for_confirmation(self, merchant, since_iso, timeout_ms) -> OrderConfirmation:
        raw = InboxMessage(
            id=f"msg_{int(time.time())}", from_addr=f"orders@{merchant}",
            subject="Your order is confirmed",
            text="Thanks for your order! Order ORD-123456 received.",
            received_at=since_iso,
        )
        return OrderConfirmation(matched=True, order_id="ORD-123456", merchant=merchant, raw=raw)

    async def list_messages(self, limit: int = 10) -> list[InboxMessage]:
        return []

    async def reply(self, message_id: str, text: str) -> None:
        return None
