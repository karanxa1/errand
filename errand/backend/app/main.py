"""Errand FastAPI backend (long-running, NOT serverless).

Owns all secrets + integrations. Exposes:
  GET  /health
  GET  /api/models                 model selector options (Sol/Terra/Luna)
  GET  /api/config                 client-safe config (Prava publishable key)
  POST /api/errand/stream          SSE: run the errand, stream every step
  POST /api/errand/{id}/approve    resolve the human-in-the-loop approval gate

The frontend (Next.js) calls these; secrets never reach the browser.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.brokers import build_brokers
from app.config import settings
from app.orchestrator.guards import ApprovalDecision
from app.orchestrator.run_errand import run_errand
from app.orchestrator.stream import EventStream
from app.voice.relay import voice_ws

app = FastAPI(title="Errand Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory approval gates keyed by run id. Each run awaits its Future until the
# frontend POSTs /approve (after the operator confirms + passkey). The Future
# resolves with a typed ApprovalDecision (approved / declined+reason / timeout).
_approvals: dict[str, asyncio.Future[ApprovalDecision]] = {}

# Human-in-the-loop gate timeout (seconds). Expiry emits `approval.timeout`.
APPROVAL_TIMEOUT_S = 300


MODELS = [
    {"key": "sol", "label": "Sol", "tagline": "Flagship — most capable", "id": "gpt-5.6-sol"},
    {"key": "terra", "label": "Terra", "tagline": "Balanced — everyday", "id": "gpt-5.6-terra"},
    {"key": "luna", "label": "Luna", "tagline": "Fastest — lightweight", "id": "gpt-5.6-luna"},
]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.websocket("/api/voice/ws")
async def voice_ws_route(websocket: WebSocket) -> None:
    # Deepgram Voice Agent relay + tool bridge. The backend holds the Deepgram
    # WS (browser tokens are FORBIDDEN on our key) and relays audio/events.
    await voice_ws(websocket)


@app.get("/api/models")
async def models() -> dict:
    return {"models": MODELS, "default": "sol"}


@app.get("/api/config")
async def config() -> dict:
    # Client-safe values only.
    return {"pravaPublishableKey": settings.prava_publishable_key}


class ErrandRequest(BaseModel):
    profile: str = "business"
    intent: str = "Restock the office pantry, under $200, approved brands only."
    user_id: str = "u_demo"
    user_email: str = "operator@example.com"
    model: str = "sol"


@app.post("/api/errand/stream")
async def errand_stream(req: ErrandRequest) -> StreamingResponse:
    run_id = uuid.uuid4().hex
    approval_id = uuid.uuid4().hex  # distinct from run_id; correlates the gate.
    stream = EventStream()
    brokers = build_brokers()

    fut: asyncio.Future[ApprovalDecision] = asyncio.get_running_loop().create_future()
    _approvals[run_id] = fut

    # Cancel token: lets the SSE generator abort an in-flight run cleanly when
    # the client disconnects (loop/hang safety).
    cancel = asyncio.Event()

    async def approve(_payload: dict) -> ApprovalDecision:
        # Emit the approval request to the client, then block until /approve.
        await stream.emit_raw(
            "approval.request", {"run_id": run_id, "approval_id": approval_id, **_payload}
        )
        try:
            decision = await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT_S)
        except asyncio.TimeoutError:
            await stream.emit_raw(
                "approval.timeout",
                {
                    "run_id": run_id,
                    "approval_id": approval_id,
                    "timeout_s": APPROVAL_TIMEOUT_S,
                },
            )
            return ApprovalDecision(approved=False, approval_id=approval_id, timed_out=True)
        # Stamp the run's approval_id onto whatever /approve resolved with.
        return ApprovalDecision(
            approved=decision.approved,
            approval_id=approval_id,
            reason=decision.reason,
            timed_out=decision.timed_out,
        )

    async def run() -> None:
        # Announce run id first so the client can target /approve.
        await stream.emit_raw("run.started", {"run_id": run_id, "model": req.model})
        try:
            outcome = await run_errand(
                brokers,
                profile=req.profile,  # type: ignore[arg-type]
                intent=req.intent,
                user_id=req.user_id,
                user_email_fallback=req.user_email,
                emit=stream.emit,
                approve=approve,
                cancel=cancel,
            )
            await stream.emit_raw("run.done", outcome)
        except asyncio.CancelledError:
            # Cooperative cancellation (client gone / server shutdown).
            await stream.emit_raw("run.error", {"message": "run cancelled"})
            raise
        except Exception as e:  # surface errors as a stream event, never 500 mid-stream
            await stream.emit_raw("run.error", {"message": str(e)})
        finally:
            _approvals.pop(run_id, None)
            await stream.close()

    task = asyncio.create_task(run())

    async def body():
        try:
            async for frame in stream.drain():
                yield frame
        finally:
            # Always tear down: signal cancel, unblock any pending gate, and
            # ensure the background task is finished so nothing leaks.
            cancel.set()
            pending = _approvals.pop(run_id, None)
            if pending is not None and not pending.done():
                pending.set_result(
                    ApprovalDecision(approved=False, approval_id=approval_id, reason="stream closed")
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


class ApproveRequest(BaseModel):
    approved: bool = True
    reason: str | None = None


@app.post("/api/errand/{run_id}/approve")
async def approve_errand(run_id: str, req: ApproveRequest) -> dict:
    fut = _approvals.get(run_id)
    if fut is None or fut.done():
        return {"ok": False, "reason": "no pending approval for this run"}
    # approval_id is stamped by the stream's approve() wrapper; pass a decision
    # carrying the operator's verdict + optional typed decline reason.
    fut.set_result(
        ApprovalDecision(approved=req.approved, approval_id=run_id, reason=req.reason)
    )
    return {"ok": True, "approved": req.approved}
