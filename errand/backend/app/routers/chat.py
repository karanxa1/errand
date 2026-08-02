"""Real AI chat over SSE.

The assistant (gpt-5.6-{sol|terra|luna} via the OpenAI-compatible endpoint)
answers normally AND can call two tools:

  - run_errand(intent, profile?)  -> the existing purchasing orchestrator, whose
    AuditEvents are streamed to the browser (same tool cards as before) and
    saved as a role='tool' message so the conversation re-renders later.
  - web_search(query, depth?)     -> Linkup grounded search.

Wire (client <-> server is streaming-only):

  POST /api/conversations/{id}/chat   body {content}
    SSE frames:
      token            {text}                 incremental assistant text
      tool.call        {name, args}
      <errand events>  run.started ... run.done  (each AuditEvent, type=step)
      approval.request {run_id, approval_id, ...}
      websearch.result {query, answer, sources}
      tool.result      {name, summary}
      assistant.saved  {message_id, content}
      title            {title}                (first turn auto-title)
      done             {}
      error            {message}

  POST /api/conversations/{id}/approve  body {run_id, approved, reason?}
    resolves the human-in-the-loop gate for an in-flight errand.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Literal

import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.brokers import build_brokers
from app.brokers.linkup import LinkupSearchBroker
from app.config import settings
from app.contracts import AuditEvent
from app.db import SessionLocal, get_session
from app.models import Approval, Conversation, Message, User
from app.orchestrator.guards import ApprovalDecision, cancellable_sleep
from app.orchestrator.run_errand import run_errand
from app.orchestrator.shop_decide import make_shop_decide
from app.orchestrator.stream import EventStream

router = APIRouter(prefix="/api/conversations", tags=["chat"])

_MODEL_MAP = {"sol": "gpt-5.6-sol", "terra": "gpt-5.6-terra", "luna": "gpt-5.6-luna"}
_DEFAULT_MODEL = "sol"
APPROVAL_TIMEOUT_S = 300

# The reasoning-effort ladder gpt-5.6 accepts. This is the MODEL's set, which is
# narrower than the endpoint's: /v1/chat/completions also accepts "minimal", but
# gpt-5.6 does not, and sending a value a model rejects is an HTTP 400 rather
# than a degraded answer. Kept next to this family rather than as one shared
# enum, because the correct set and the correct down-map differ per model.
# https://developers.openai.com/api/docs/models/gpt-5.6-sol
GPT56_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")

# What we must send whenever the request carries `tools`. gpt-5.6 defaults to
# "medium", and OpenAI documents function tools on /v1/chat/completions as
# compatible only with effective reasoning "none" for this family — so the
# default is the unsafe value and this is not optional.
# https://developers.openai.com/api/docs/guides/upgrading-to-gpt-5p6-sol
TOOL_REASONING_EFFORT = "none"

# Ceiling on generated tokens. gpt-5.6's maximum output is 128k; a single wedged
# turn should not be able to spend that. `max_completion_tokens`, never
# `max_tokens` — the reference marks the latter deprecated in favour of it.
# https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
MAX_COMPLETION_TOKENS = 4096

# Approval gates for chat-driven errands are DB-backed (table `approvals`, see
# app/models.py), scoped by conversation_id. The stream INSERTs a `pending` row,
# then polls it; POST /{id}/approve UPDATEs it in a separate request. Resolving a
# gate is scoped to a conversation the caller provably owns: /{id}/approve calls
# _owned() FIRST (authorizing the conversation), and only then UPDATEs
# WHERE scope=conversation_id — so a run_id from a DIFFERENT conversation (i.e.
# another user's spend) matches zero rows even if its run_id was learned.
#
# ⚠️ SCALING: the RENDEZVOUS is now shared, but the RUN is not.
# Same story as app.main: the (conversation_id, run_id) hand-off is durable in
# Postgres, so /approve landing on a different replica or uvicorn worker than the
# SSE stream is now correct — the stream's next poll (≤ ~1s) sees the write. That
# removes the old single-worker footgun for the approval hand-off. BUT the
# run_errand coroutine, its brokers, the cancel token and the streaming queue all
# still live in THIS process's heap, so a restart mid-run still loses that state
# and the in-flight run is NOT resumable. The deployment stays pinned to
# min=max=1 replica / single worker for that reason; this change makes the
# approval rendezvous horizontally correct, not the run itself.

# How often the SSE stream re-reads its pending approval row (~1s; a human gate
# does not need tighter). Mirrors app.main._APPROVAL_POLL_INTERVAL_S.
_APPROVAL_POLL_INTERVAL_S = 1.0

SYSTEM_PROMPT = (
    "You are Errand, a warm, concise assistant that chats naturally and can run "
    "real purchasing errands.\n"
    "ANSWER, DON'T INTERROGATE. When the user asks something, go and find out. "
    "Reach for web_search whenever the answer depends on anything current — "
    "prices, availability, specs, recommendations, news — rather than hedging or "
    "asking them to narrow it down. If a request is slightly underspecified, "
    "make the sensible assumption, say which assumption you made in one clause, "
    "and answer. Ask a clarifying question ONLY when the readings genuinely lead "
    "somewhere different and you cannot pick, or when money is about to move. "
    "Never bounce a question back that you could have researched.\n"
    "When the user wants something bought, ordered, or restocked, call the "
    "run_errand tool with their request verbatim as `intent` and set `profile` "
    "('business' for work/office, 'personal' for the user's own items). Don't "
    "quiz them about budget or merchant first — the spend policy supplies both, "
    "and the errand pauses for their approval before any money moves. If the user "
    "names a spend limit ('under $10', 'keep it below $25'), pass it as "
    "`max_cents` in CENTS (under $10 → 1000). If they see the cart and ask for a "
    "cheaper one, call run_errand AGAIN with a NEW, lower `max_cents` — do not "
    "just re-send the same call, or you will get the same cart back. After it "
    "returns, tell them the result plainly; never invent an order.\n"
    "SPEND IS THE EXCEPTION: before anything is charged the user sees the exact "
    "amount and merchant and approves it on screen with a passkey. That gate is "
    "never skipped or assumed. If an errand fails, say specifically what failed "
    "and what would fix it — not just that it failed.\n"
    "Keep replies short and human."
)

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_errand",
            "description": (
                "Run a real purchasing errand end to end: load the spend policy, "
                "build a cart, create a payment session, PAUSE for the user's "
                "approval, then check out. Use for any request to buy/order/restock."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string", "description": "The purchase request, verbatim."},
                    "profile": {
                        "type": "string",
                        "enum": ["business", "personal"],
                        "description": "'business' for work/office, 'personal' for own items.",
                    },
                    "max_cents": {
                        "type": "integer",
                        "description": (
                            "Optional spend cap in CENTS for THIS order, from an "
                            "explicit user limit like 'under $10' (=1000) or 'keep "
                            "it below $25' (=2500). Only lowers spend; the policy "
                            "budget is still the ceiling. Omit if the user gave no "
                            "amount. Re-send a NEW, lower value when the user asks "
                            "for a cheaper cart."
                        ),
                        "minimum": 1,
                    },
                },
                "required": ["intent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the live web for current facts, prices, or recommendations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    # Mirrors LinkupSearchBroker.DEPTHS; see the citation there.
                    "depth": {"type": "string", "enum": list(LinkupSearchBroker.DEPTHS)},
                },
                "required": ["query"],
            },
        },
    },
]

# The live-handoff tool is APPENDED only when the feature is configured (Cloudflare
# creds present + flag on). Off by default, so the model never offers a capability
# the deployment can't perform. See settings.live_handoff_ready.
_SHOP_LIVE_TOOL = {
    "type": "function",
    "function": {
        "name": "shop_live",
        "description": (
            "Shop a real merchant site in a live browser and hand it to the user "
            "to log in and PAY THEMSELVES. Use when the user wants to buy from a "
            "specific real store (especially one needing an account/login) rather "
            "than the policy errand. The agent fills the cart, then the user "
            "completes payment in the live browser — the agent never enters card "
            "details. `merchant_url` is the store to shop."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "merchant_url": {"type": "string", "description": "The store URL to shop."},
                "intent": {"type": "string", "description": "What to buy, verbatim."},
            },
            "required": ["merchant_url", "intent"],
        },
    },
}


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    # Only read when this turn is the one that materializes the conversation (see
    # _owned_or_created). On every later turn the stored row already carries the
    # operator's choices and these are ignored, so a client cannot silently
    # rewrite a conversation's profile/model through the chat path — that is what
    # PATCH /api/conversations/{id} is for.
    profile: Literal["business", "personal"] = "business"
    model: Literal["sol", "terra", "luna"] = "sol"
    # Stable per-BROWSER id, minted once client-side and persisted
    # (lib/deviceProfile.ts). Unlike profile/model this is read on EVERY turn,
    # not just the first: it describes the device this turn is being sent from,
    # and that is a property of now, not of the conversation row.
    #
    # Safe to accept from the client because it identifies the device, not the
    # spender — the buyer still comes from the bearer token. Forwarded to Prava
    # so a repeat buyer reads as the same device; a fresh value each checkout
    # forces another passkey registration and burns one of a hard-capped number
    # of token bindings.
    browser_profile_id: str | None = None


class ApproveRequest(BaseModel):
    run_id: str
    approved: bool = True
    reason: str | None = None


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


# The agentic-shop decision step is SHARED by the chat path and the voice relay,
# so it lives in app.orchestrator.shop_decide (one implementation, both callers)
# rather than here. Imported at module top.


async def _owned(session: AsyncSession, user: User, conversation_id: str) -> Conversation:
    convo = await session.get(Conversation, conversation_id)
    if convo is None or convo.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return convo


# A client-generated conversation id must look exactly like a server-generated
# one — uuid4().hex, i.e. 32 lowercase hex characters — before it is allowed to
# become a primary key. Without this a caller could seed the table with arbitrary
# 32-char strings, and any id shorter than the column would be silently
# truncated by some backends into a collision with an existing row.
_CLIENT_CONVERSATION_ID = re.compile(r"\A[0-9a-f]{32}\Z")


async def _owned_or_created(
    session: AsyncSession,
    user: User,
    conversation_id: str,
    *,
    profile: str,
    model: str,
) -> Conversation:
    """The caller's conversation, materializing it on first use.

    The frontend generates a conversation id locally, puts it in the URL, and
    starts streaming immediately — there is no blocking POST /api/conversations
    ahead of the first token. The row is therefore created here, on the first
    turn that actually has something to say, which also means abandoning a new
    chat without typing leaves no empty row behind.

    An id that already exists and belongs to someone else is reported as 404, not
    403: a caller must not be able to probe which conversation ids exist.
    """
    convo = await session.get(Conversation, conversation_id)
    if convo is not None:
        if convo.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        return convo

    if not _CLIENT_CONVERSATION_ID.match(conversation_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    convo = Conversation(
        id=conversation_id, user_id=user.id, profile=profile, model=model
    )
    session.add(convo)
    try:
        await session.flush()
    except IntegrityError:
        # Two first turns raced for the same id (a double-submit, or a retry that
        # overlapped the original). The other one won and the row now exists, so
        # roll this attempt back and adopt it rather than failing the turn.
        await session.rollback()
        convo = await session.get(Conversation, conversation_id)
        if convo is None or convo.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            ) from None
    return convo


@router.post("/{conversation_id}/approve")
async def approve_chat(
    conversation_id: str,
    req: ApproveRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # _owned() FIRST: it raises 404 unless the caller owns THIS conversation.
    # Only then do we UPDATE WHERE scope=conversation_id — that ordering is the
    # authorization, so a run_id from someone else's conversation matches zero
    # rows even if the attacker learned it. The "no pending" response is
    # identical to "not resolvable", so it cannot be used to probe run ids.
    await _owned(session, user, conversation_id)
    result = await session.execute(
        update(Approval)
        .where(
            Approval.scope == conversation_id,
            Approval.run_id == req.run_id,
            Approval.status == "pending",
        )
        .values(
            status="approved" if req.approved else "declined",
            reason=req.reason,
            resolved_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    if result.rowcount == 0:
        return {"ok": False, "reason": "no pending approval for this run"}
    return {"ok": True, "approved": req.approved}


@router.post("/{conversation_id}/chat")
async def chat(
    conversation_id: str,
    req: ChatRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    convo = await _owned_or_created(
        session, user, conversation_id, profile=req.profile, model=req.model
    )

    # Persist the user's message + load prior turns for context.
    user_msg = Message(conversation_id=convo.id, role="user", content=req.content)
    session.add(user_msg)
    convo.updated_at = datetime.now(timezone.utc)
    await session.commit()

    prior = list(
        await session.scalars(
            select(Message).where(Message.conversation_id == convo.id).order_by(Message.created_at)
        )
    )
    # `prior` is read AFTER the commit above, so it already contains the message
    # just inserted. On the genuine first turn that is the only user message, so
    # the count is exactly 1 and `<= 1` is the correct first-turn test (not an
    # off-by-one). `<=` rather than `==` so a conversation whose history was
    # trimmed to zero user rows still auto-titles instead of silently never
    # titling.
    is_first_turn = sum(1 for m in prior if m.role == "user") <= 1

    convo_id = convo.id
    profile = convo.profile if convo.profile in ("business", "personal") else "business"
    model_id = _MODEL_MAP.get(convo.model, _MODEL_MAP[_DEFAULT_MODEL])

    # Build the OpenAI message list from history (tool messages are context too).
    oai_messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in prior:
        if m.role == "tool":
            # Represent a past tool run compactly as an assistant note.
            oai_messages.append({"role": "assistant", "content": m.content or "(ran an errand)"})
        else:
            oai_messages.append({"role": m.role, "content": m.content})

    stream = EventStream()

    # Cancel token: lets the SSE generator abort an in-flight errand cleanly when
    # the client disconnects, so the orchestrator stops at its next step boundary
    # instead of only unwinding on hard task cancellation. Mirrors main.py.
    cancel = asyncio.Event()

    async def emit_errand(ev: AuditEvent, run_id: str) -> None:
        payload = ev.model_dump()
        payload["type"] = ev.step
        payload["run_id"] = run_id
        await stream.emit_raw(ev.step, payload)

    async def approve(run_id: str, approval_id: str, payload: dict) -> ApprovalDecision:
        # Insert a pending gate row scoped to this conversation, emit the request,
        # then poll the row from a FRESH session (the request session is closed
        # once the body streams) until /approve resolves it, the client
        # disconnects, or APPROVAL_TIMEOUT_S elapses.
        async with SessionLocal() as s:
            s.add(Approval(scope=convo_id, run_id=run_id, status="pending"))
            await s.commit()
        await stream.emit_raw(
            "approval.request", {"run_id": run_id, "approval_id": approval_id, **payload}
        )
        deadline = time.monotonic() + APPROVAL_TIMEOUT_S
        try:
            while True:
                if cancel.is_set():
                    return ApprovalDecision(
                        approved=False, approval_id=approval_id, reason="stream closed"
                    )
                if time.monotonic() >= deadline:
                    await stream.emit_raw(
                        "approval.timeout", {"run_id": run_id, "timeout_s": APPROVAL_TIMEOUT_S}
                    )
                    return ApprovalDecision(
                        approved=False, approval_id=approval_id, timed_out=True
                    )
                async with SessionLocal() as s:
                    row = (
                        await s.scalars(
                            select(Approval).where(
                                Approval.scope == convo_id, Approval.run_id == run_id
                            )
                        )
                    ).first()
                    row_status = row.status if row is not None else None
                    row_reason = row.reason if row is not None else None
                if row is None:
                    # Deleted by teardown: reads as a closed stream, not a decline.
                    return ApprovalDecision(
                        approved=False, approval_id=approval_id, reason="stream closed"
                    )
                if row_status != "pending":
                    return ApprovalDecision(
                        approved=(row_status == "approved"),
                        approval_id=approval_id,
                        reason=row_reason,
                        timed_out=(row_status == "timeout"),
                    )
                await cancellable_sleep(_APPROVAL_POLL_INTERVAL_S, cancel)
        finally:
            # Always drop this run's gate, including on cancellation (client
            # disconnect), so the table can't accumulate dead rows.
            async with SessionLocal() as s:
                await s.execute(
                    delete(Approval).where(
                        Approval.scope == convo_id, Approval.run_id == run_id
                    )
                )
                await s.commit()

    async def do_web_search(args: dict) -> str:
        query = (args.get("query") or "").strip()
        depth = args.get("depth") or "standard"
        if not query:
            return "No search query was provided."
        broker = LinkupSearchBroker(settings.linkup_api_key, settings.linkup_api_base)
        result = await broker.search(query, depth=depth)
        answer = result.get("answer") or ""
        sources = result.get("sources") or []
        await stream.emit_raw(
            "websearch.result", {"query": query, "answer": answer, "sources": sources}
        )
        titles = [s.get("name", "") for s in sources[:3] if s.get("name")]
        return f"{answer}\n\nSources: {', '.join(titles)}" if titles else (answer or "No results.")

    async def do_run_errand(args: dict) -> tuple[str, list[dict]]:
        intent = (args.get("intent") or "").strip()
        p = args.get("profile") or profile
        if p not in ("business", "personal"):
            p = "business"
        if not intent:
            return "I need to know what to buy first.", []

        # Optional user spend cap. Coerce defensively — the model can hand back a
        # string, a float, or a nonsense value — and drop anything non-positive so
        # a bad cap never blocks the run; run_errand treats None as "no cap".
        max_cents: int | None = None
        raw_cap = args.get("max_cents")
        if raw_cap is not None:
            try:
                parsed = int(float(raw_cap))
                if parsed > 0:
                    max_cents = parsed
            except (TypeError, ValueError):
                max_cents = None

        run_id = uuid.uuid4().hex
        approval_id = uuid.uuid4().hex
        brokers = build_brokers()
        collected: list[dict] = []

        async def emit(ev: AuditEvent) -> None:
            collected.append({**ev.model_dump(), "type": ev.step, "run_id": run_id})
            await emit_errand(ev, run_id)

        async def approve_wrap(payload: dict) -> ApprovalDecision:
            return await approve(run_id, approval_id, payload)

        # Live browser view. Each shop action pushes ONE browser.frame carrying a
        # base64 JPEG of the page + a caption. Throttled to ~2/s so a fast loop
        # cannot flood the SSE stream, and the frame is dropped (not queued) if it
        # arrives inside the throttle window — the LATEST frame is what matters,
        # never a backlog. The reducer keeps only the most recent frame, so this
        # is bounded end to end.
        import base64

        last_frame_at = 0.0

        async def on_frame(step: str, detail: str, shot: bytes | None) -> None:
            nonlocal last_frame_at
            if not shot:
                # No page to screenshot (the wallet/real-merchant path drives no
                # browser we own) — surface the step as an audit line so the
                # thread still shows live progress, then stop. Not a browser.frame:
                # a frame with no image would blank the live view.
                await emit_errand(
                    AuditEvent(
                        at=datetime.now(timezone.utc).isoformat(),
                        step=step,
                        detail=detail,
                        data={},
                    ),
                    run_id,
                )
                collected.append({"type": step, "run_id": run_id, "detail": detail})
                return
            now = time.monotonic()
            if now - last_frame_at < 0.5:
                return
            last_frame_at = now
            b64 = base64.b64encode(shot).decode("ascii")
            await stream.emit_raw(
                "browser.frame",
                {"run_id": run_id, "mime": "image/jpeg", "b64": b64, "caption": detail},
            )

        shop_decide = make_shop_decide(model_id, reasoning_effort=TOOL_REASONING_EFFORT)

        await stream.emit_raw("run.started", {"run_id": run_id, "model": model_id})
        collected.append({"type": "run.started", "run_id": run_id, "model": model_id})
        try:
            outcome = await run_errand(
                brokers,
                profile=p,  # type: ignore[arg-type]
                intent=intent,
                user_id=f"u_{user.id[:12]}",
                user_email_fallback=user.email,
                browser_profile_id=req.browser_profile_id,
                emit=emit,
                approve=approve_wrap,
                cancel=cancel,
                max_cents=max_cents,
                shop_decide=shop_decide,
                on_frame=on_frame,
            )
        except asyncio.CancelledError:
            # Client disconnected / stream torn down. Must propagate so the task
            # actually dies; swallowing it here would turn a cancelled run into a
            # "the errand failed" tool result fed back to the model.
            raise
        except Exception as e:  # noqa: BLE001
            # The provider CODE, not just the prose — see main.py for why.
            err: dict = {"run_id": run_id, "message": str(e)}
            code = getattr(e, "code", None)
            if isinstance(code, str) and code:
                err["code"] = code
            await stream.emit_raw("run.error", err)
            collected.append({"type": "run.error", **err})
            return f"The errand failed: {e}", collected
        outcome = {**outcome, "run_id": run_id}
        await stream.emit_raw("run.done", outcome)
        collected.append({"type": "run.done", **outcome})
        return _summarize(outcome), collected

    async def do_shop_live(args: dict) -> tuple[str, list[dict]]:
        """Agent shops a real store in a live browser; the USER pays in it.

        Distinct from run_errand: no Prava card, no policy budget filter minting a
        card — the human performs the payment in the handed-off live view. The
        human's 'Done paying'/'Cancel' resolves the SAME approval rendezvous the
        errand uses (approved = finished, declined = cancelled)."""
        merchant_url = (args.get("merchant_url") or "").strip()
        intent = (args.get("intent") or "").strip()
        if not merchant_url or not intent:
            return "I need both a store URL and what to buy.", []
        if not settings.live_handoff_ready:  # defence in depth; the tool is gated already
            return "Live browser handoff isn't enabled on this deployment.", []

        run_id = uuid.uuid4().hex
        approval_id = uuid.uuid4().hex
        collected: list[dict] = []
        import base64

        from app.brokers.shopper import CloudflareShopperBroker, ShopperError

        async def emit(ev: AuditEvent) -> None:
            collected.append({**ev.model_dump(), "type": ev.step, "run_id": run_id})
            await emit_errand(ev, run_id)

        last_frame_at = 0.0

        async def on_frame(step: str, detail: str, shot: bytes | None) -> None:
            nonlocal last_frame_at
            if not shot:
                await emit(AuditEvent(
                    at=datetime.now(timezone.utc).isoformat(), step=step, detail=detail, data={}
                ))
                return
            now = time.monotonic()
            if now - last_frame_at < 0.5:
                return
            last_frame_at = now
            await stream.emit_raw(
                "browser.frame",
                {"run_id": run_id, "mime": "image/jpeg", "b64": base64.b64encode(shot).decode("ascii"), "caption": detail},
            )

        async def on_live_view(url: str) -> None:
            # The interactive URL for the human. The frontend renders it as an
            # iframe + 'open in new tab' and shows the 'Done paying' control.
            await stream.emit_raw("browser.liveview", {"run_id": run_id, "url": url})
            collected.append({"type": "browser.liveview", "run_id": run_id, "url": url})

        async def wait_for_human() -> dict:
            # Reuse the errand approval gate as the 'done paying / cancel' signal.
            decision = await approve(
                run_id, approval_id,
                {"kind": "live_handoff", "merchant_url": merchant_url},
            )
            return {"approved": decision.approved}

        shop_decide = make_shop_decide(model_id, reasoning_effort=TOOL_REASONING_EFFORT)
        # A real store the agent shops isn't scoped by the Senso policy, so an
        # unbounded budget + no rules lets the model pick freely; the human pays,
        # so spend control is the human's payment step, not a policy cap here.
        from app.contracts import PurchaseContext
        ctx = PurchaseContext(profile=p, approved_merchants=[], budget_cents=10**9, rules=[])

        await stream.emit_raw("run.started", {"run_id": run_id, "model": model_id})
        collected.append({"type": "run.started", "run_id": run_id, "model": model_id})
        try:
            shopper = CloudflareShopperBroker()
            order = await shopper.shop_live_handoff(
                merchant_url, intent, ctx,
                decide=shop_decide,
                wait_for_human=wait_for_human,
                on_frame=on_frame,
                on_live_view=on_live_view,
            )
        except asyncio.CancelledError:
            raise
        except ShopperError as e:
            err = {"run_id": run_id, "message": str(e), "code": getattr(e, "step", "")}
            await stream.emit_raw("run.error", err)
            collected.append({"type": "run.error", **err})
            return f"The live checkout didn't complete: {e}", collected
        except Exception as e:  # noqa: BLE001
            err = {"run_id": run_id, "message": str(e)}
            await stream.emit_raw("run.error", err)
            collected.append({"type": "run.error", **err})
            return f"The live checkout failed: {e}", collected

        outcome = {"run_id": run_id, "kind": "completed", "order_id": order.order_id}
        await stream.emit_raw("run.done", outcome)
        collected.append({"type": "run.done", **outcome})
        return (
            f"You completed the checkout in the live browser. {order.confirmation_text}"
            if order.order_id == "placed"
            else f"Order {order.order_id} placed. {order.confirmation_text}"
        ), collected

    async def run() -> None:
        final_text = ""
        tool_events_all: list[dict] = []
        # Text streamed on the most recent pass. If the tool loop exhausts its
        # iteration cap, this is the prose the user actually SAW, so it is what
        # must be persisted (see the loop's `else:` branch).
        last_pass_text = ""
        try:
            # `async with` closes the client's httpx connection pool on EVERY exit
            # path (normal, error, cancellation). Previously the client was built
            # per request and never closed, leaking a pool + sockets each turn.
            # Offer the live-handoff tool only when the deployment can actually
            # perform it, so the model never promises a capability that isn't wired.
            active_tools = _TOOLS + ([_SHOP_LIVE_TOOL] if settings.live_handoff_ready else [])
            async with _client() as client:
                # Tool-calling loop: keep going until the model returns plain text.
                for _ in range(6):
                    completion = await client.chat.completions.create(
                        model=model_id,
                        messages=oai_messages,
                        tools=active_tools,
                        stream=True,
                        # gpt-5.6 defaults to "medium", and OpenAI documents
                        # medium-with-function-tools on /v1/chat/completions as
                        # unsupported for this family — it is an HTTP 400, not a
                        # degraded answer. See TOOL_REASONING_EFFORT.
                        reasoning_effort=TOOL_REASONING_EFFORT,
                        max_completion_tokens=MAX_COMPLETION_TOKENS,
                    )
                    text_parts: list[str] = []
                    tool_calls: dict[int, dict] = {}
                    async for chunk in completion:
                        delta = chunk.choices[0].delta if chunk.choices else None
                        if delta is None:
                            continue
                        if delta.content:
                            text_parts.append(delta.content)
                            await stream.emit_raw("token", {"text": delta.content})
                        for tc in delta.tool_calls or []:
                            slot = tool_calls.setdefault(
                                tc.index, {"id": "", "name": "", "arguments": ""}
                            )
                            if tc.id:
                                slot["id"] = tc.id
                            if tc.function and tc.function.name:
                                slot["name"] = tc.function.name
                            if tc.function and tc.function.arguments:
                                slot["arguments"] += tc.function.arguments

                    last_pass_text = "".join(text_parts).strip()

                    if not tool_calls:
                        final_text = last_pass_text
                        break

                    # Record the assistant's tool-call turn, then execute each call.
                    oai_messages.append(
                        {
                            "role": "assistant",
                            "content": "".join(text_parts) or None,
                            "tool_calls": [
                                {
                                    "id": c["id"] or f"call_{i}",
                                    "type": "function",
                                    "function": {
                                        "name": c["name"],
                                        "arguments": c["arguments"] or "{}",
                                    },
                                }
                                for i, c in sorted(tool_calls.items())
                            ],
                        }
                    )
                    for i, c in sorted(tool_calls.items()):
                        name = c["name"]
                        try:
                            cargs = json.loads(c["arguments"] or "{}")
                        except json.JSONDecodeError:
                            cargs = {}
                        await stream.emit_raw("tool.call", {"name": name, "args": cargs})
                        # A tool failure is reported TO THE MODEL as the tool
                        # result, not raised: every tool call the model made must
                        # get a matching tool message or the next request is
                        # malformed, and one broken tool should not discard the
                        # whole turn (including any assistant text already
                        # streamed). do_run_errand handles its own errors;
                        # do_web_search can raise on a Linkup/network failure.
                        try:
                            if name == "run_errand":
                                result, events = await do_run_errand(cargs)
                                tool_events_all.extend(events)
                            elif name == "shop_live":
                                result, events = await do_shop_live(cargs)
                                tool_events_all.extend(events)
                            elif name == "web_search":
                                result = await do_web_search(cargs)
                            else:
                                result = f"Unknown tool: {name}"
                        except asyncio.CancelledError:
                            raise
                        except Exception as tool_err:  # noqa: BLE001
                            result = f"Tool {name} failed: {tool_err}"
                        await stream.emit_raw(
                            "tool.result", {"name": name, "summary": result}
                        )
                        oai_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": c["id"] or f"call_{i}",
                                "content": result,
                            }
                        )
                else:
                    # for/else: reached only when the loop was NOT broken out of,
                    # i.e. all 6 passes returned tool calls and the model never
                    # settled on plain text. `final_text` is still "" here (it is
                    # only assigned on the break path), so prefer whatever prose
                    # the last pass streamed — the user already saw those tokens,
                    # and persisting the generic fallback instead would make the
                    # reloaded conversation disagree with the live stream.
                    final_text = last_pass_text or "I've completed the steps above."

            # Persist assistant + tool records in a fresh session (request session
            # is closed once the response body starts streaming). `async with`
            # closes/rolls back this session on any error too.
            async with SessionLocal() as s:
                if tool_events_all:
                    s.add(
                        Message(
                            conversation_id=convo_id,
                            role="tool",
                            content=_last_summary(tool_events_all),
                            events=tool_events_all,
                        )
                    )
                assistant_msg = Message(
                    conversation_id=convo_id, role="assistant", content=final_text
                )
                s.add(assistant_msg)
                convo_row = await s.get(Conversation, convo_id)
                if convo_row is not None:
                    convo_row.updated_at = datetime.now(timezone.utc)
                    if is_first_turn:
                        convo_row.title = _make_title(req.content)
                        await stream.emit_raw("title", {"title": convo_row.title})
                await s.commit()
                await s.refresh(assistant_msg)
                await stream.emit_raw(
                    "assistant.saved",
                    {"message_id": assistant_msg.id, "content": final_text},
                )
            await stream.emit_raw("done", {})
        except asyncio.CancelledError:
            # The client disconnected and body()'s finally cancelled us. Nothing
            # is draining the queue, so emitting would be pointless; re-raise so
            # the task terminates as cancelled rather than looking successful.
            raise
        except Exception as e:  # noqa: BLE001
            await stream.emit_raw("error", {"message": str(e)})
        finally:
            await stream.close()

    task = asyncio.create_task(run())

    async def body():
        try:
            async for frame in stream.drain():
                yield frame
        finally:
            # Always tear down so the background task can never outlive the
            # request: signal cooperative cancel (which the poll loop observes
            # and returns from) and delete any of this conversation's approval
            # rows (otherwise a torn-down turn could leave a row behind), then
            # hard-cancel and await the task so it is fully finished before the
            # response ends.
            cancel.set()
            try:
                async with SessionLocal() as s:
                    await s.execute(
                        delete(Approval).where(Approval.scope == convo_id)
                    )
                    await s.commit()
            except Exception:  # noqa: BLE001 — teardown must never raise
                pass
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def _summarize(outcome: dict) -> str:
    kind = outcome.get("kind")
    if kind == "completed":
        oid = outcome.get("order_id") or outcome.get("confirmation_order_id") or "?"
        total = outcome.get("total_cents")
        if isinstance(total, int):
            return f"Order {oid} placed for ${total / 100:.2f}."
        return f"Order {oid} placed."
    reason = outcome.get("reason") or "the errand stopped."
    if kind == "aborted":
        return f"Stopped the errand: {reason}"
    if kind == "failed":
        return f"The errand failed: {reason}"
    return reason


def _last_summary(events: list[dict]) -> str:
    for ev in reversed(events):
        if ev.get("type") == "run.done":
            return _summarize(ev)
    return "Ran an errand."


def _make_title(text: str) -> str:
    t = " ".join(text.split())
    return (t[:48] + "…") if len(t) > 48 else (t or "New chat")
