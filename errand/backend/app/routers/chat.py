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

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.brokers import build_brokers
from app.brokers.linkup import LinkupSearchBroker
from app.config import settings
from app.contracts import AuditEvent
from app.db import SessionLocal, get_session
from app.models import Conversation, Message, User
from app.orchestrator.guards import ApprovalDecision
from app.orchestrator.run_errand import run_errand
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

# In-memory approval gates for chat-driven errands, keyed by (conversation_id,
# run_id). The conversation id is part of the key so resolving a gate is scoped
# to a conversation the caller provably owns: _owned() authorizes the
# conversation, and the key then makes it impossible to reach a run belonging to
# a DIFFERENT conversation (i.e. another user's spend) by supplying its run_id.
#
# ⚠️ SCALING CONSTRAINT — THIS REQUIRES EXACTLY ONE PROCESS.
# Same constraint as app.main._approvals, for the same reason: the Future lives
# in THIS process's heap, but the SSE stream that awaits it and the
# POST /{id}/approve that resolves it are separate HTTP requests. If they land on
# different replicas or different uvicorn workers, /approve finds no Future,
# returns ok:false, and the errand hangs until APPROVAL_TIMEOUT_S then aborts the
# spend — the user's approval appears to do nothing. A restart mid-gate likewise
# loses the Future and the run aborts on timeout, unresumable.
# The deployment is pinned to min=max=1 replica (and a single worker) precisely
# to satisfy this, so it is correct as deployed. Horizontal scaling would need a
# shared rendezvous (Redis pub/sub, Postgres LISTEN/NOTIFY, or a DB approvals
# table the stream polls) — deliberately NOT done here.
_approvals: dict[tuple[str, str], asyncio.Future[ApprovalDecision]] = {}

SYSTEM_PROMPT = (
    "You are Errand, a warm, concise assistant that chats naturally and can run "
    "real purchasing errands. Answer general questions directly. When the user "
    "wants something bought, ordered, or restocked, call the run_errand tool "
    "with their request verbatim as `intent` and set `profile` ('business' for "
    "work/office, 'personal' for the user's own items). The errand pauses for "
    "the user's approval before any spend — after it returns, tell them the "
    "result plainly; never invent an order. Use web_search for current facts, "
    "prices, or product recommendations. Keep replies short and human."
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


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    # Only read when this turn is the one that materializes the conversation (see
    # _owned_or_created). On every later turn the stored row already carries the
    # operator's choices and these are ignored, so a client cannot silently
    # rewrite a conversation's profile/model through the chat path — that is what
    # PATCH /api/conversations/{id} is for.
    profile: Literal["business", "personal"] = "business"
    model: Literal["sol", "terra", "luna"] = "sol"


class ApproveRequest(BaseModel):
    run_id: str
    approved: bool = True
    reason: str | None = None


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


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
    await _owned(session, user, conversation_id)
    # Keyed by (conversation_id, run_id): _owned() proved the caller owns THIS
    # conversation, so a run_id from someone else's conversation cannot be
    # resolved here even if the attacker learned it.
    fut = _approvals.get((conversation_id, req.run_id))
    if fut is None or fut.done():
        return {"ok": False, "reason": "no pending approval for this run"}
    fut.set_result(
        ApprovalDecision(approved=req.approved, approval_id=req.run_id, reason=req.reason)
    )
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
        fut: asyncio.Future[ApprovalDecision] = asyncio.get_running_loop().create_future()
        key = (convo_id, run_id)
        _approvals[key] = fut
        await stream.emit_raw(
            "approval.request", {"run_id": run_id, "approval_id": approval_id, **payload}
        )
        try:
            decision = await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT_S)
        except asyncio.TimeoutError:
            await stream.emit_raw(
                "approval.timeout", {"run_id": run_id, "timeout_s": APPROVAL_TIMEOUT_S}
            )
            return ApprovalDecision(approved=False, approval_id=approval_id, timed_out=True)
        finally:
            # Always drop the gate, including on cancellation (client
            # disconnect), so the map can't accumulate dead Futures.
            _approvals.pop(key, None)
        return ApprovalDecision(
            approved=decision.approved,
            approval_id=approval_id,
            reason=decision.reason,
            timed_out=decision.timed_out,
        )

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

        run_id = uuid.uuid4().hex
        approval_id = uuid.uuid4().hex
        brokers = build_brokers()
        collected: list[dict] = []

        async def emit(ev: AuditEvent) -> None:
            collected.append({**ev.model_dump(), "type": ev.step, "run_id": run_id})
            await emit_errand(ev, run_id)

        async def approve_wrap(payload: dict) -> ApprovalDecision:
            return await approve(run_id, approval_id, payload)

        await stream.emit_raw("run.started", {"run_id": run_id, "model": model_id})
        collected.append({"type": "run.started", "run_id": run_id, "model": model_id})
        try:
            outcome = await run_errand(
                brokers,
                profile=p,  # type: ignore[arg-type]
                intent=intent,
                user_id=f"u_{user.id[:12]}",
                user_email_fallback=user.email,
                emit=emit,
                approve=approve_wrap,
                cancel=cancel,
            )
        except asyncio.CancelledError:
            # Client disconnected / stream torn down. Must propagate so the task
            # actually dies; swallowing it here would turn a cancelled run into a
            # "the errand failed" tool result fed back to the model.
            raise
        except Exception as e:  # noqa: BLE001
            await stream.emit_raw("run.error", {"run_id": run_id, "message": str(e)})
            collected.append({"type": "run.error", "run_id": run_id, "message": str(e)})
            return f"The errand failed: {e}", collected
        outcome = {**outcome, "run_id": run_id}
        await stream.emit_raw("run.done", outcome)
        collected.append({"type": "run.done", **outcome})
        return _summarize(outcome), collected

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
            async with _client() as client:
                # Tool-calling loop: keep going until the model returns plain text.
                for _ in range(6):
                    completion = await client.chat.completions.create(
                        model=model_id,
                        messages=oai_messages,
                        tools=_TOOLS,
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
            # request: signal cooperative cancel, unblock any pending approval
            # gate for this conversation (otherwise the run sits on its Future
            # until APPROVAL_TIMEOUT_S even though nobody is listening), then
            # hard-cancel and await the task so it is fully finished before the
            # response ends.
            cancel.set()
            for (c_id, r_id), pending in list(_approvals.items()):
                if c_id == convo_id and not pending.done():
                    pending.set_result(
                        ApprovalDecision(
                            approved=False, approval_id=r_id, reason="stream closed"
                        )
                    )
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
