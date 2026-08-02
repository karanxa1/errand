"""Shared domain models — the single source of truth for the seams.

Every broker implements a Protocol here; the orchestrator depends only on these
Protocols, never on concrete implementations. Swapping a mock broker for a real
one must require zero changes outside the broker module.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

# ── Context / profiles ──────────────────────────────────────────────────────

ProfileKind = Literal["business", "personal"]


class Citation(BaseModel):
    source: str
    snippet: str


class PurchaseContext(BaseModel):
    profile: ProfileKind
    approved_merchants: list["Merchant"] = Field(default_factory=list)
    budget_cents: int
    rules: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class Merchant(BaseModel):
    name: str
    url: str


# ── Shopping ─────────────────────────────────────────────────────────────────

class CartItem(BaseModel):
    name: str
    qty: int
    price_cents: int


class CheckoutState(BaseModel):
    merchant_url: str
    items: list[CartItem]
    session_ref: str


class CartResult(BaseModel):
    items: list[CartItem]
    total_cents: int
    checkout: CheckoutState


class OrderResult(BaseModel):
    order_id: str
    confirmation_text: str
    screenshot_url: str | None = None


# ── Payment (Prava) ──────────────────────────────────────────────────────────

class PaymentCredential(BaseModel):
    token: str
    dynamic_cvv: str
    expiry_month: str  # "MM"
    expiry_year: str  # "YYYY"
    txn_ref_id: str


class CreateSessionInput(BaseModel):
    merchant: Merchant
    total_cents: int
    user_id: str
    user_email: str
    items: list[CartItem]


class CreateSessionResult(BaseModel):
    session_id: str
    iframe_url: str


class PollPending(BaseModel):
    status: Literal["pending"] = "pending"


class PollCompleted(BaseModel):
    status: Literal["completed"] = "completed"
    credential: PaymentCredential


class PollFailed(BaseModel):
    status: Literal["failed"] = "failed"
    code: str
    message: str


PollCredentialResult = PollPending | PollCompleted | PollFailed
TxnStatus = Literal["APPROVED", "DECLINED"]


# ── Email (AgentMail) ─────────────────────────────────────────────────────────

class InboxMessage(BaseModel):
    id: str
    from_addr: str
    subject: str
    text: str
    received_at: str
    attachments: list[dict] = Field(default_factory=list)


class OrderConfirmation(BaseModel):
    matched: bool
    order_id: str | None = None
    total_cents: int | None = None
    merchant: str | None = None
    raw: InboxMessage | None = None


# ── Audit ─────────────────────────────────────────────────────────────────────

class AuditEvent(BaseModel):
    at: str  # ISO
    step: str
    detail: str
    data: dict | None = None


# ── Broker Protocols (the seams) ──────────────────────────────────────────────

class ContextBroker(Protocol):
    async def get_context(self, profile: ProfileKind, intent: str) -> PurchaseContext: ...


class ShopperBroker(Protocol):
    async def build_cart(
        self, merchant_url: str, intent: str, context: PurchaseContext
    ) -> CartResult: ...
    async def complete_checkout(
        self, checkout: CheckoutState, credential: PaymentCredential
    ) -> OrderResult: ...


class PaymentBroker(Protocol):
    async def create_session(self, data: CreateSessionInput) -> CreateSessionResult: ...
    async def poll_credential(self, session_id: str) -> PollCredentialResult: ...
    async def report_status(
        self, session_id: str, txn_ref_id: str, status: TxnStatus
    ) -> None: ...


class MailBroker(Protocol):
    async def ensure_inbox(self) -> str: ...  # returns address
    async def wait_for_confirmation(
        self, merchant: str, since_iso: str, timeout_ms: int
    ) -> OrderConfirmation: ...
    async def list_messages(self, limit: int = 10) -> list[InboxMessage]: ...
    async def reply(self, message_id: str, text: str) -> None: ...


PurchaseContext.model_rebuild()
