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
    if settings.prava_shop_ready:
        # The wallet shopper buys from REAL merchants in Prava's catalog, so the
        # demonstration storefront is not a substitute for anything — its host is
        # not indexed and never will be. Leave the policy's URL alone and let
        # build_cart fail saying the approved merchant is not shoppable, rather
        # than silently pointing a live card at a fake store.
        return merchant, None
    return (
        Merchant(name=merchant.name, url=settings.demo_store_url),
        merchant.url,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _shop_the_ladder(
    brokers: Brokers,
    ctx,
    intent: str,
    *,
    rec: Callable[..., Awaitable[None]],
    guard: Callable[[str], Awaitable[None]],
    discovery_allowed: bool,
) -> tuple[Merchant, "object"] | None:
    """Try the approved vendors, then (if allowed) whoever else stocks it.

    Returns the merchant that actually filled the cart alongside that cart, or
    None if nobody could. The pairing is the point: the caller mints a card
    scoped to the merchant it is handed, and a card scoped to a merchant we did
    not shop declines at the network — silently, and only after the human has
    already approved.
    """
    attempts = 0
    tried: set[str] = set()
    failures: list[str] = []

    async def attempt(candidate: Merchant, source: str) -> tuple[Merchant, object] | None:
        nonlocal attempts
        resolved, substituted_from = resolve_merchant(candidate)
        key = resolved.url.lower()
        if key in tried:
            return None
        tried.add(key)
        attempts += 1

        if substituted_from is not None:
            # The policy's vendor URL was unroutable and got swapped. SAY SO: the
            # record must never imply the policy named the URL we shopped, and
            # the card below is scoped to the substitute, so silence here would
            # be a misstatement about where money went.
            await rec(
                "context.merchant_resolved",
                (
                    f"Policy vendor URL {substituted_from} is not routable; "
                    f"shopping the demonstration storefront instead."
                ),
                {
                    "merchant_name": resolved.name,
                    "policy_url": substituted_from,
                    "resolved_url": resolved.url,
                },
            )

        await guard("cart.attempt")
        try:
            cart = await brokers.shopper.build_cart(resolved.url, intent, ctx)
        except (RunCancelled, StepBudgetExceeded):
            raise
        except Exception as exc:  # noqa: BLE001 — any shopper failure is "not here"
            failures.append(f"{resolved.name}: {exc}")
            await rec(
                "cart.merchant_unavailable",
                f"{resolved.name} could not fill this errand: {exc}",
                {"merchant": resolved.model_dump(), "source": source},
            )
            return None
        if source != "policy":
            await rec(
                "cart.merchant_discovered",
                (
                    f"No approved vendor stocked this, so it was sourced from "
                    f"{resolved.name} — a merchant the policy did not name. "
                    f"Approve or decline below."
                ),
                {"merchant": resolved.model_dump(), "tried": sorted(tried)},
            )
        return resolved, cart

    # Rung 1: every vendor the policy approved, in the order it listed them.
    for candidate in ctx.approved_merchants:
        if attempts >= settings.max_merchant_attempts:
            break
        found = await attempt(candidate, "policy")
        if found is not None:
            return found

    if not discovery_allowed:
        return None

    # Rung 2: whoever else has it. Only a shopper with a real catalog can answer
    # this; the storefront and mock shoppers simply have no wider world.
    discover = getattr(brokers.shopper, "discover_merchants", None)
    if discover is None:
        return None
    remaining = max(0, settings.max_merchant_attempts - attempts)
    if remaining == 0:
        return None
    try:
        candidates = await discover(intent, ctx, remaining)
    except Exception as exc:  # noqa: BLE001 — discovery is a bonus, never fatal
        await rec("cart.discovery_failed", f"Could not search for other vendors: {exc}")
        return None

    for candidate in candidates:
        if attempts >= settings.max_merchant_attempts:
            break
        found = await attempt(candidate, "discovered")
        if found is not None:
            return found
    return None


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
    # Stable per-browser identity from the client. Forwarded to Prava so a repeat
    # buyer reads as the SAME device: a fresh id each checkout forces another
    # passkey registration and burns one of a hard-capped number of token
    # bindings, which is how a card ends up permanently at "Maximum binding for
    # token exceeded".
    browser_profile_id: str | None = None,
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

        discovery_allowed = settings.discovery_allowed(profile)
        if not ctx.approved_merchants and not discovery_allowed:
            await rec("context.no_merchant", "No approved merchant; stopping.")
            return {"kind": "aborted", "reason": "No approved merchant in context."}

        # 2. Shop; park on checkout.
        #
        # An approved vendor being out of stock is ordinary, not exceptional, so
        # this is a LADDER rather than a single attempt: every merchant the
        # policy approved is tried in turn, and only if all of them come up empty
        # does the search widen to Prava's catalog — and only when the profile
        # allows that (see Settings.merchant_discovery). Each rung is recorded.
        # "We bought it somewhere else" must never be something the operator
        # learns from their statement.
        shopped = await _shop_the_ladder(
            brokers,
            ctx,
            intent,
            rec=rec,
            guard=guard,
            discovery_allowed=discovery_allowed,
        )
        if shopped is None:
            await rec(
                "cart.unavailable",
                "No approved merchant could fill this errand"
                + ("." if discovery_allowed else ", and vendor discovery is off for this profile."),
            )
            return {
                "kind": "aborted",
                "reason": "Nothing matching the request was available to buy.",
            }
        merchant, cart = shopped

        await rec(
            "cart.built",
            f"Cart at {merchant.name}: {len(cart.items)} items, "
            f"total ${cart.total_cents/100:.2f}",
            {**cart.model_dump(), "merchant": merchant.model_dump()},
        )
        if cart.total_cents > ctx.budget_cents:
            await rec("cart.over_budget", "Cart exceeds budget; stopping.")
            return {"kind": "aborted", "reason": "Cart exceeds budget."}

        # 3. Prava session (pins merchant + amount).
        await guard("payment.session")
        # Belt and braces on the pairing above: if a shopper ever returns a cart
        # parked at a different merchant than the one it was asked for, the card
        # would be scoped to the wrong place. Cheap to check, and the failure it
        # prevents is invisible until a decline lands post-approval.
        cart_host = (urlparse(cart.checkout.merchant_url).hostname or "").lower()
        pinned_host = (urlparse(merchant.url).hostname or "").lower()
        if cart_host and pinned_host and cart_host.removeprefix("www.") != pinned_host.removeprefix("www."):
            await rec(
                "cart.merchant_mismatch",
                f"Cart is parked at {cart_host} but the card would be scoped to "
                f"{pinned_host}; refusing to mint a card for the wrong merchant.",
                {"cart_host": cart_host, "pinned_host": pinned_host},
            )
            return {"kind": "aborted", "reason": "Merchant mismatch between cart and card."}
        session = await brokers.payment.create_session(
            CreateSessionInput(
                # `merchant` is the one that actually filled the cart, not the
                # first name on the policy list. A Prava card is scoped to this
                # merchant at the network, so handing it a different one buys a
                # guaranteed decline — after the human has already approved.
                merchant=merchant,
                total_cents=cart.total_cents,
                user_id=user_id,
                # The HUMAN'S registered address, not the agent's inbox.
                #
                # These are two different jobs and were conflated. The agent
                # inbox exists so the mail broker can catch the MERCHANT'S order
                # confirmation. Prava's `user_email` is the CUSTOMER identity: it
                # is forwarded to the card network during passkey registration
                # and is where a verification code goes. Sending the agent's
                # mailbox registered the person's passkey against an address
                # they do not own and cannot read — and if the OTP ever routes
                # there, the human simply never receives it.
                user_email=user_email_fallback or address,
                items=cart.items,
                browser_profile_id=browser_profile_id,
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
