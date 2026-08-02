"""The persona-agnostic engine. Same flow for business and personal — only the
profile (and thus the grounded context) differs. Depends only on broker
Protocols. Emits an AuditEvent at every meaningful step via `emit`, which the
API layer streams to the client in real time (no client polling).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

from urllib.parse import urlparse

from app.brokers import Brokers
from app.config import settings
from app.contracts import (
    AuditEvent,
    CreateSessionInput,
    Merchant,
    PollCompleted,
    PollFailed,
    ProfileKind,
    TxnStatus,
)
from app.orchestrator.guards import (
    ApprovalDecision,
    RunCancelled,
    StepBudget,
    StepBudgetExceeded,
    cancellable_sleep,
    check_cancel,
)

Emit = Callable[[AuditEvent], Awaitable[None]]

# Approval gate: resolves when the operator approves in the UI (+ passkey).
# Returns a typed decision (approved/declined-with-reason/timed-out).
ApprovalFn = Callable[[dict], Awaitable[ApprovalDecision]]

# Loop/hang safety defaults. The linear flow is ~10 steps; the cap is a backstop
# against a looping caller, not a tight bound. Credential polling gets a hard
# wall-clock so a stuck session can never hang the SSE stream indefinitely.
DEFAULT_MAX_STEPS = 40
DEFAULT_CREDENTIAL_WAIT_S = 90.0
_POLL_INTERVAL_S = 3.0


def resolve_merchant(merchant: Merchant) -> tuple[Merchant, str | None]:
    """Swap an unroutable policy URL for the demonstration storefront.

    Returns ``(merchant, original_url_or_None)``. The second element is non-None
    ONLY when a substitution happened, so the caller can record it.

    The seeded policy's vendor URL is on `example.com`, which IANA reserves for
    documentation — it can never serve a storefront, so the shopper found no
    products and no errand could ever complete. Only the hosts named in
    `settings.unroutable_merchant_hosts` are ever rewritten, and the merchant's
    NAME is left exactly as Senso stated it: this changes where we shop, never
    who the policy says is approved.
    """
    host = (urlparse(merchant.url).hostname or "").lower()
    if host not in settings.unroutable_hosts:
        return merchant, None
    return (
        Merchant(name=merchant.name, url=settings.demo_store_url),
        merchant.url,
    )


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
    cancel: asyncio.Event | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    credential_wait_s: float = DEFAULT_CREDENTIAL_WAIT_S,
) -> dict:
    budget = StepBudget(max_steps)

    async def rec(step: str, detail: str, data: dict | None = None) -> None:
        await emit(AuditEvent(at=_now(), step=step, detail=detail, data=data))

    async def guard(step: str) -> None:
        # Cooperative cancel + step-cap check at the top of every step.
        check_cancel(cancel, step)
        budget.tick(step)

    try:
        # 0. Agent inbox (used as the order/contact email).
        await guard("inbox.ready")
        address = await brokers.mail.ensure_inbox()
        await rec("inbox.ready", f"Agent inbox: {address}", {"address": address})

        # 1. Ground the decision (Senso).
        await guard("context.loaded")
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

        # The policy's vendor URL may be unroutable (see resolve_merchant). If it
        # is swapped, SAY SO in the audit trail: the record must never imply the
        # policy named the URL we actually shopped. The Prava session below is
        # pinned to this resolved merchant, so the substitution is also what the
        # card is scoped to — which makes silence here a misstatement about spend.
        merchant, substituted_from = resolve_merchant(merchant)
        if substituted_from is not None:
            await rec(
                "context.merchant_resolved",
                (
                    f"Policy vendor URL {substituted_from} is not routable; "
                    f"shopping the demonstration storefront instead."
                ),
                {
                    "merchant_name": merchant.name,
                    "policy_url": substituted_from,
                    "resolved_url": merchant.url,
                },
            )

        # 2. Shop; park on checkout.
        await guard("cart.built")
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
        await guard("payment.session")
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
        await guard("approval")
        decision = await approve(
            {"context": ctx.model_dump(), "cart": cart.model_dump(), "session": session.model_dump()}
        )
        if not decision.approved:
            # No credential/txn_ref exists yet, so there is nothing to report to
            # Prava here — the session was never advanced to a transaction.
            if decision.timed_out:
                # main.py already emitted approval.timeout; record the abort.
                await rec(
                    "approval.denied",
                    "Approval gate timed out; spend aborted.",
                    {"approval_id": decision.approval_id, "timed_out": True},
                )
                return {
                    "kind": "aborted",
                    "reason": "Approval timed out.",
                    "approval_id": decision.approval_id,
                }
            detail = "Operator declined the spend."
            if decision.reason:
                detail = f"Operator declined the spend: {decision.reason}"
            await rec(
                "approval.denied",
                detail,
                {"approval_id": decision.approval_id, "reason": decision.reason},
            )
            return {
                "kind": "aborted",
                "reason": "Spend not approved.",
                "approval_id": decision.approval_id,
                "decline_reason": decision.reason,
            }
        await rec(
            "approval.granted",
            "Operator approved the spend (passkey).",
            {"approval_id": decision.approval_id},
        )

        # 5. Poll for the one-time credential (server-side; hidden from client).
        #    Bounded by a hard wall-clock and the cancel token.
        credential = None
        started_at = _now()
        deadline = time.monotonic() + credential_wait_s
        while time.monotonic() < deadline:
            check_cancel(cancel, "payment.poll")
            res = await brokers.payment.poll_credential(session.session_id)
            if isinstance(res, PollCompleted):
                credential = res.credential
                break
            if isinstance(res, PollFailed):
                # Prava reported failure before issuing a credential: no txn_ref
                # yet, so nothing to report — surface it and stop.
                await rec("payment.failed", res.message, {"code": res.code})
                return {"kind": "failed", "reason": f"Payment failed: {res.message}"}
            if await cancellable_sleep(_POLL_INTERVAL_S, cancel):
                check_cancel(cancel, "payment.poll")
        if credential is None:
            await rec("payment.timeout", "Credential not ready in time.")
            return {"kind": "failed", "reason": "Payment credential timed out."}
        await rec(
            "payment.credential",
            "One-time credential issued.",
            {"last4": credential.token[-4:], "txn_ref_id": credential.txn_ref_id},
        )

        # 6-7. Checkout + MANDATORY report-status. From here a txn_ref exists, so
        #      Prava must ALWAYS hear the outcome (APPROVED or DECLINED) — a
        #      credential left unreported strands the transaction in
        #      awaiting_result. Pessimistic default: DECLINED unless checkout
        #      succeeds. try/finally guarantees exactly one report.
        status: TxnStatus = "DECLINED"
        order = None
        checkout_error: str | None = None
        try:
            order = await brokers.shopper.complete_checkout(cart.checkout, credential)
            status = "APPROVED"
            await rec("checkout.completed", order.confirmation_text, order.model_dump())
        except Exception as e:  # noqa: BLE001 — any checkout failure => DECLINED
            checkout_error = str(e)
        finally:
            # Shield the report so an in-flight cancellation can't skip it and
            # strand the transaction. Report failures are surfaced, not raised.
            try:
                await asyncio.shield(
                    brokers.payment.report_status(session.session_id, credential.txn_ref_id, status)
                )
                await rec(
                    "payment.reported",
                    f"Reported {status} to Prava.",
                    {"status": status, "txn_ref_id": credential.txn_ref_id},
                )
            except Exception as report_err:  # noqa: BLE001
                await rec(
                    "payment.report_failed",
                    f"Could not report {status} to Prava: {report_err}",
                    {"status": status, "txn_ref_id": credential.txn_ref_id},
                )

        if status == "DECLINED" or order is None:
            # Checkout raised after the credential was issued: we've already
            # reported DECLINED above; now surface the error as a stream event.
            await rec(
                "payment.declined",
                f"Checkout failed after credential issued: {checkout_error}",
                {"txn_ref_id": credential.txn_ref_id, "error": checkout_error},
            )
            return {
                "kind": "failed",
                "reason": f"Checkout failed: {checkout_error}",
                "txn_ref_id": credential.txn_ref_id,
                "reported_status": status,
            }

        # 8. Close the loop: catch the confirmation email.
        await guard("mail.confirmation")
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

    except (RunCancelled, StepBudgetExceeded) as abort:
        # Loop/hang safety: clean, reported abort rather than a wedged run.
        await rec("run.aborted", str(abort), {"reason": type(abort).__name__})
        return {"kind": "aborted", "reason": str(abort)}
