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
import logging
import uuid

from fastapi import Depends, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from contextlib import asynccontextmanager

from app.auth import get_current_user
from app.brokers import build_brokers
from app.config import settings
from app.db import init_db
from app.models import User
from app.orchestrator.guards import ApprovalDecision
from app.orchestrator.run_errand import run_errand
from app.orchestrator.stream import EventStream
from app.routers import auth as auth_router
from app.routers import chat as chat_router
from app.routers import conversations as conversations_router
from app.voice.relay import voice_ws


logger = logging.getLogger("errand.startup")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fail fast on an unsafe JWT signing secret. In a real deployment this is a
    # hard stop: booting with the published dev default would let anyone forge a
    # bearer token for any user. In dev it is a loud warning so local work isn't
    # blocked.
    problem = settings.jwt_secret_problem
    if problem is not None:
        if settings.is_dev:
            logger.warning("INSECURE JWT CONFIG (allowed because ENVIRONMENT=dev): %s", problem)
        else:
            raise RuntimeError(
                f"Refusing to start with an insecure JWT secret: {problem} "
                "Set the JWT_SECRET env var to a long random string."
            )
    # Create tables for SQLite dev; a no-op where Alembic already built them.
    await init_db()
    yield


app = FastAPI(title="Errand Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(conversations_router.router)
app.include_router(chat_router.router)

# In-memory approval gates keyed by run id. Each run awaits its Future until the
# frontend POSTs /approve (after the operator confirms + passkey). The Future
# resolves with a typed ApprovalDecision (approved / declined+reason / timeout).
#
# ⚠️ SCALING CONSTRAINT — THIS REQUIRES EXACTLY ONE PROCESS.
# The Future lives in THIS process's heap. The SSE stream that awaits it and the
# POST /approve that resolves it are two separate HTTP requests, so they must be
# routed to the same process for the gate to ever open. Consequences of scaling
# out or restarting:
#   - replicas > 1: /approve lands on a replica that has no Future for that
#     run_id, returns {"ok": false, "reason": "no pending approval..."} and the
#     real run on the other replica hangs until APPROVAL_TIMEOUT_S, then aborts
#     the spend. The user sees their approval silently do nothing.
#   - uvicorn --workers > 1 (or gunicorn with multiple workers) breaks this the
#     same way, for the same reason. Keep it single-worker.
#   - a rolling deploy / crash mid-gate loses the Future, so the in-flight run
#     aborts on timeout. It cannot be resumed.
# The deployment is pinned to min=max=1 replica precisely to satisfy this, so it
# is correct as deployed. Making this horizontally scalable needs a shared
# rendezvous (Redis pub/sub, LISTEN/NOTIFY, or a DB-backed approvals table with
# the stream polling it) — deliberately NOT done here.
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
    #
    # ⚠️ KNOWN GAP — this endpoint is UNAUTHENTICATED. It consumes Deepgram +
    # OpenAI credits and can reach run_errand (real spend, gated on the browser
    # confirming approval). It is not fixed here because the browser WebSocket API
    # cannot set an Authorization header, so auth would have to move to a query
    # param or a first-message handshake — and the frontend (lib/useVoiceAgent.ts)
    # currently opens this socket with no credential at all, so adding a check
    # would break live voice. Closing this properly needs a coordinated
    # frontend+backend change (mint a short-lived ticket over authenticated HTTP,
    # then pass it as ?ticket=... and validate on accept). Tracked, not silent.
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
    model: str = "sol"
    # NOTE: user_id / user_email are deliberately NOT accepted from the client.
    # This endpoint spends real money, so the spender's identity is taken from
    # the bearer token, not from the request body — otherwise a caller could
    # attribute a purchase to someone else. See errand_stream below.


@app.post("/api/errand/stream")
async def errand_stream(
    req: ErrandRequest, user: User = Depends(get_current_user)
) -> StreamingResponse:
    # AUTH REQUIRED: this drives the real purchasing orchestrator (Prava payment
    # session + checkout) and burns OpenAI/Linkup credits. It was previously
    # unauthenticated, meaning anyone who could reach the backend could initiate
    # spend. The identity below is derived from the verified token.
    user_id = f"u_{user.id[:12]}"
    user_email = user.email
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
                user_id=user_id,
                user_email_fallback=user_email,
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
async def approve_errand(
    run_id: str, req: ApproveRequest, _user: User = Depends(get_current_user)
) -> dict:
    # AUTH REQUIRED: this resolves a spend approval gate. Previously anyone who
    # learned a run_id could approve (or decline) someone else's purchase.
    fut = _approvals.get(run_id)
    if fut is None or fut.done():
        return {"ok": False, "reason": "no pending approval for this run"}
    # approval_id is stamped by the stream's approve() wrapper; pass a decision
    # carrying the operator's verdict + optional typed decline reason.
    fut.set_result(
        ApprovalDecision(approved=req.approved, approval_id=run_id, reason=req.reason)
    )
    return {"ok": True, "approved": req.approved}
