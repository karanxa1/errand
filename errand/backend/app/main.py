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
from app.routers import voice as voice_router
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
app.include_router(voice_router.router)

# In-memory approval gates keyed by (owner user id, run id). Each run awaits its
# Future until the frontend POSTs /approve (after the operator confirms +
# passkey). The Future resolves with a typed ApprovalDecision (approved /
# declined+reason / timeout).
#
# ⚠️ THE USER ID IS PART OF THE KEY, AND THAT IS THE AUTHORIZATION.
# Authenticating /approve is necessary but not sufficient: with a run_id-only key
# ANY signed-in account that learned a run_id could resolve someone else's spend
# gate — approve a stranger's purchase, or decline it. Since the key carries the
# owner, a lookup performed with the caller's own id simply cannot reach another
# user's run, so a leaked run_id is inert in anyone else's hands. This mirrors
# app.routers.chat._approvals, which is keyed (conversation_id, run_id) for the
# same reason.
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
_approvals: dict[tuple[str, str], asyncio.Future[ApprovalDecision]] = {}

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
    # AUTH REQUIRED, via ?ticket=... rather than a bearer header: the browser
    # WebSocket API cannot set headers, so the client POSTs the authenticated
    # /api/voice/ticket (routers/voice.py) and presents the one-shot ticket here.
    # voice_ws redeems it before opening anything, so an anonymous socket can no
    # longer burn Deepgram + OpenAI credits or reach run_errand (real spend); an
    # invalid or replayed ticket is closed 4401. The redeemed ticket also carries
    # the spender's identity into the relay, replacing the old hardcoded demo
    # user. Single-process constraint: see app/voice/tickets.py.
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

    # The gate is owned by the caller who started the run; only they can resolve
    # it. See the _approvals comment above for why the id is in the key.
    gate_key = (user.id, run_id)
    fut: asyncio.Future[ApprovalDecision] = asyncio.get_running_loop().create_future()
    _approvals[gate_key] = fut

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
            _approvals.pop(gate_key, None)
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
            pending = _approvals.pop(gate_key, None)
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
    run_id: str, req: ApproveRequest, user: User = Depends(get_current_user)
) -> dict:
    # AUTH REQUIRED, AND SCOPED TO THE CALLER. The gate is looked up under the
    # caller's OWN user id, so a run_id belonging to someone else resolves to no
    # gate at all. Authenticating alone would not have been enough: it would still
    # have let any signed-in account approve a stranger's purchase with nothing
    # but a leaked run_id. The response for "not yours" is deliberately identical
    # to "no such run", so this cannot be used to probe which runs exist.
    fut = _approvals.get((user.id, run_id))
    if fut is None or fut.done():
        return {"ok": False, "reason": "no pending approval for this run"}
    # approval_id is stamped by the stream's approve() wrapper; pass a decision
    # carrying the operator's verdict + optional typed decline reason.
    fut.set_result(
        ApprovalDecision(approved=req.approved, approval_id=run_id, reason=req.reason)
    )
    return {"ok": True, "approved": req.approved}
