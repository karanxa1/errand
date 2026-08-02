"""The persona-agnostic engine. Same flow for business and personal — only the
profile (and thus the grounded context) differs. Depends only on broker
Protocols. Emits an AuditEvent at every meaningful step via `emit`, which the
API layer streams to the client in real time (no client polling).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.brokers import Brokers
from app.contracts import (
    AuditEvent,
    CreateSessionInput,
    PollCompleted,
    PollFailed,
    ProfileKind,
)

Emit = Callable[[AuditEvent], Awaitable[None]]

# Approval gate: resolves when the operator approves in the UI (+ passkey).
ApprovalFn = Callable[[dict], Awaitable[bool]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_errand(
    brokers: Brokers,
    *,
    profile: ProfileKind,
    intent: str,
    user_id: str,
    user_email_fallback: str,
    emit: Emit,
    approve: ApprovalFn,
) -> dict:
    async def rec(step: str, detail: str, data: dict | None = None) -> None:
        await emit(AuditEvent(at=_now(), step=step, detail=detail, data=data))

    # 0. Agent inbox (used as the order/contact email).
    address = await brokers.mail.ensure_inbox()
    await rec("inbox.ready", f"Agent inbox: {address}", {"address": address})

    # 1. Ground the decision (Senso).
    ctx = await brokers.context.get_context(profile, intent)
    await rec(
        "context.loaded",
        f"Loaded {profile} context: budget ${ctx.budget_cents/100:.2f}, {len(ctx.rules)} rules",
        ctx.model_dump(),
    )

    if not ctx.approved_merchants:
        await rec("context.no_merchant", "No approved merchant; stopping.")
        return {"kind": "aborted", "reason": "No approved merchant in context."}
    merchant = ctx.approved_merchants[0]

    # 2. Shop; park on checkout.
    cart = await brokers.shopper.build_cart(merchant.url, intent, ctx)
    await rec(
        "cart.built",
        f"Cart: {len(cart.items)} items, total ${cart.total_cents/100:.2f}",
        cart.model_dump(),
    )
    if cart.total_cents > ctx.budget_cents:
        await rec("cart.over_budget", "Cart exceeds budget; stopping.")
        return {"kind": "aborted", "reason": "Cart exceeds budget."}

    # 3. Prava session (pins merchant + amount).
    session = await brokers.payment.create_session(
        CreateSessionInput(
            merchant=merchant,
            total_cents=cart.total_cents,
            user_id=user_id,
            user_email=address or user_email_fallback,
            items=cart.items,
        )
    )
    await rec("payment.session", f"Prava session {session.session_id}", session.model_dump())

    # 4. Human-in-the-loop approval (+ passkey in UI).
    approved = await approve(
        {"context": ctx.model_dump(), "cart": cart.model_dump(), "session": session.model_dump()}
    )
    if not approved:
        await rec("approval.denied", "Operator declined the spend.")
        return {"kind": "aborted", "reason": "Spend not approved."}
    await rec("approval.granted", "Operator approved the spend (passkey).")

    # 5. Poll for the one-time credential (server-side; hidden from client).
    credential = None
    started_at = _now()
    for _ in range(30):
        res = await brokers.payment.poll_credential(session.session_id)
        if isinstance(res, PollCompleted):
            credential = res.credential
            break
        if isinstance(res, PollFailed):
            await rec("payment.failed", res.message, {"code": res.code})
            return {"kind": "failed", "reason": f"Payment failed: {res.message}"}
        await asyncio.sleep(3)
    if credential is None:
        await rec("payment.timeout", "Credential not ready in time.")
        return {"kind": "failed", "reason": "Payment credential timed out."}
    await rec(
        "payment.credential",
        "One-time credential issued.",
        {"last4": credential.token[-4:], "txn_ref_id": credential.txn_ref_id},
    )

    # 6. Complete real checkout with the credential.
    order = await brokers.shopper.complete_checkout(cart.checkout, credential)
    await rec("checkout.completed", order.confirmation_text, order.model_dump())

    # 7. Report outcome to Prava (required).
    await brokers.payment.report_status(session.session_id, credential.txn_ref_id, "APPROVED")
    await rec("payment.reported", "Reported APPROVED to Prava.")

    # 8. Close the loop: catch the confirmation email.
    confirmation = await brokers.mail.wait_for_confirmation(
        merchant=merchant.url, since_iso=started_at, timeout_ms=30_000
    )
    await rec(
        "mail.confirmation",
        f"Confirmation email {'received: ' + (confirmation.order_id or '?') if confirmation.matched else 'not matched'}",
        confirmation.model_dump(),
    )

    return {
        "kind": "completed",
        "order_id": order.order_id,
        "total_cents": cart.total_cents,
        "confirmation_order_id": confirmation.order_id,
    }
