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
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select
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

# In-memory approval gates for chat-driven errands, keyed by run_id.
_approvals: dict[str, asyncio.Future[ApprovalDecision]] = {}

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
                    "depth": {"type": "string", "enum": ["standard", "deep"]},
                },
                "required": ["query"],
            },
        },
    },
]


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


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


@router.post("/{conversation_id}/approve")
async def approve_chat(
    conversation_id: str,
    req: ApproveRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _owned(session, user, conversation_id)
    fut = _approvals.get(req.run_id)
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
    convo = await _owned(session, user, conversation_id)

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

    async def emit_errand(ev: AuditEvent, run_id: str) -> None:
        payload = ev.model_dump()
        payload["type"] = ev.step
        payload["run_id"] = run_id
        await stream.emit_raw(ev.step, payload)

    async def approve(run_id: str, approval_id: str, payload: dict) -> ApprovalDecision:
        fut: asyncio.Future[ApprovalDecision] = asyncio.get_running_loop().create_future()
        _approvals[run_id] = fut
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
            _approvals.pop(run_id, None)
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
            )
        except Exception as e:  # noqa: BLE001
            await stream.emit_raw("run.error", {"run_id": run_id, "message": str(e)})
            collected.append({"type": "run.error", "run_id": run_id, "message": str(e)})
            return f"The errand failed: {e}", collected
        outcome = {**outcome, "run_id": run_id}
        await stream.emit_raw("run.done", outcome)
        collected.append({"type": "run.done", **outcome})
        return _summarize(outcome), collected

    async def run() -> None:
        client = _client()
        final_text = ""
        tool_events_all: list[dict] = []
        try:
            # Tool-calling loop: keep going until the model returns plain text.
            for _ in range(6):
                completion = await client.chat.completions.create(
                    model=model_id,
                    messages=oai_messages,
                    tools=_TOOLS,
                    stream=True,
                    # gpt-5.6 requires reasoning_effort='none' to use function
                    # tools via /v1/chat/completions.
                    reasoning_effort="none",
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

                if not tool_calls:
                    final_text = "".join(text_parts).strip()
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
                                "function": {"name": c["name"], "arguments": c["arguments"] or "{}"},
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
                    if name == "run_errand":
                        result, events = await do_run_errand(cargs)
                        tool_events_all.extend(events)
                    elif name == "web_search":
                        result = await do_web_search(cargs)
                    else:
                        result = f"Unknown tool: {name}"
                    await stream.emit_raw("tool.result", {"name": name, "summary": result})
                    oai_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": c["id"] or f"call_{i}",
                            "content": result,
                        }
                    )
            else:
                final_text = final_text or "I've completed the steps above."

            # Persist assistant + tool records in a fresh session (request session
            # is closed once the response body starts streaming).
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
