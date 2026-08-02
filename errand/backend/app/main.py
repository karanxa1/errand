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
import time
import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from contextlib import asynccontextmanager

from app.auth import get_current_user
from app.brokers import build_brokers
from app.config import settings
from app.db import SessionLocal, get_session, init_db
from app.models import Approval, User
from app.orchestrator.guards import ApprovalDecision, cancellable_sleep
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

# Approval gates are DB-backed (table `approvals`, see app/models.py). The SSE
# stream INSERTs a `pending` row scoped to (owner user id, run id), emits
# `approval.request`, then POLLS that row from a fresh short-lived session until
# it leaves `pending`; POST /approve UPDATEs the row in a separate request. The
# await and the resolve therefore no longer have to run in the same process.
#
# ⚠️ THE USER ID IS THE SCOPE, AND THAT IS THE AUTHORIZATION.
# Authenticating /approve is necessary but not sufficient: with a run_id-only key
# ANY signed-in account that learned a run_id could resolve someone else's spend
# gate — approve a stranger's purchase, or decline it. Because the row is
# addressed by (scope=owner id, run_id), an UPDATE issued under the caller's own
# id simply cannot match another user's run, so a leaked run_id is inert in
# anyone else's hands. This mirrors the chat path, which scopes by conversation
# id (which the caller must first be proven to own) for the same reason.
#
# ⚠️ SCALING: the RENDEZVOUS is now shared, but the RUN is not.
# The (scope, run_id) hand-off is durable in Postgres, so /approve landing on a
# different replica or uvicorn worker than the SSE stream is now correct: it
# writes the row, and the stream's next poll (≤ ~1s later) observes it. That
# removes the old single-worker footgun for the approval hand-off specifically.
# BUT the run_errand coroutine itself still lives entirely in THIS process's
# heap — its cart, Prava session, cancel token and the streaming queue are all
# in-memory. A rolling deploy or crash mid-run still loses that state, so an
# in-flight run is NOT resumable across a restart; the client would have to start
# over. For that reason the deployment stays pinned to min=max=1 replica /
# single worker. This change makes the approval rendezvous horizontally correct;
# it does NOT make the run itself horizontally scalable.
APPROVAL_TIMEOUT_S = 300

# How often the SSE stream re-reads its pending approval row. ~1s is plenty for a
# human gate and keeps the poll cheap (one indexed point-read per second).
_APPROVAL_POLL_INTERVAL_S = 1.0


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


async def _await_approval_via_db(
    *,
    scope: str,
    run_id: str,
    approval_id: str,
    stream: EventStream,
    request_payload: dict,
    cancel: asyncio.Event,
) -> ApprovalDecision:
    """Insert a pending gate, emit `approval.request`, then poll the row until it
    is resolved, the client disconnects, or APPROVAL_TIMEOUT_S elapses.

    Every DB touch uses a FRESH session: by the time this runs the request-scoped
    session is long closed (the SSE body is already streaming), so a leased
    connection here would be a use-after-close. This mirrors chat.py, which opens
    SessionLocal() for exactly the same reason.
    """
    async with SessionLocal() as s:
        s.add(Approval(scope=scope, run_id=run_id, status="pending"))
        await s.commit()

    await stream.emit_raw(
        "approval.request", {"run_id": run_id, "approval_id": approval_id, **request_payload}
    )

    deadline = time.monotonic() + APPROVAL_TIMEOUT_S
    while True:
        # Client gone (body() teardown set cancel): abort the gate the same way
        # the old in-process path did on stream close.
        if cancel.is_set():
            return ApprovalDecision(
                approved=False, approval_id=approval_id, reason="stream closed"
            )
        # Wall-clock expiry: emit approval.timeout and return timed_out exactly as
        # the old asyncio.wait_for path did.
        if time.monotonic() >= deadline:
            await stream.emit_raw(
                "approval.timeout",
                {
                    "run_id": run_id,
                    "approval_id": approval_id,
                    "timeout_s": APPROVAL_TIMEOUT_S,
                },
            )
            return ApprovalDecision(approved=False, approval_id=approval_id, timed_out=True)

        async with SessionLocal() as s:
            row = (
                await s.scalars(
                    select(Approval).where(
                        Approval.scope == scope, Approval.run_id == run_id
                    )
                )
            ).first()
            status = row.status if row is not None else None
            reason = row.reason if row is not None else None

        # Row deleted out from under us (teardown) counts as a closed stream, not
        # a decline-with-reason, so it reads identically to the cancel path above.
        if row is None:
            return ApprovalDecision(
                approved=False, approval_id=approval_id, reason="stream closed"
            )
        if status != "pending":
            # Stamp the run's approval_id onto whatever /approve resolved with.
            return ApprovalDecision(
                approved=(status == "approved"),
                approval_id=approval_id,
                reason=reason,
                timed_out=(status == "timeout"),
            )

        # Sleep, but wake immediately if the client disconnects mid-wait.
        await cancellable_sleep(_APPROVAL_POLL_INTERVAL_S, cancel)


async def _delete_approval(scope: str, run_id: str) -> None:
    """Drop a run's gate row so the table can't accumulate. A late /approve after
    this correctly finds no row and answers ok:false."""
    async with SessionLocal() as s:
        await s.execute(
            delete(Approval).where(Approval.scope == scope, Approval.run_id == run_id)
        )
        await s.commit()


class ErrandRequest(BaseModel):
    profile: str = "business"
    intent: str = "Restock the office pantry, under $200, approved brands only."
    model: str = "sol"
    # Stable per-browser id, minted and persisted by the client (localStorage).
    # UNLIKE user_id/user_email this one legitimately comes from the browser:
    # it identifies the DEVICE, not the spender, and the server has no way to
    # know it. It cannot be used to attribute a purchase to anyone.
    browser_profile_id: str | None = None
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

    # The gate is scoped to the caller who started the run; only they can resolve
    # it. See the module comment above for why the owner is the scope.
    scope = user.id

    # Cancel token: lets the SSE generator abort an in-flight run cleanly when
    # the client disconnects (loop/hang safety).
    cancel = asyncio.Event()

    async def approve(_payload: dict) -> ApprovalDecision:
        # Insert a pending gate row, emit the request, then poll the DB until
        # /approve resolves it (any process), the client disconnects, or timeout.
        return await _await_approval_via_db(
            scope=scope,
            run_id=run_id,
            approval_id=approval_id,
            stream=stream,
            request_payload=_payload,
            cancel=cancel,
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
                browser_profile_id=req.browser_profile_id,
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
            # Carry the provider's own CODE alongside the message. "expired
            # code", "binding limit reached", "card verification failed" and
            # "device not supported" all collapse to "Payment failed" without
            # it — and that is the difference between a user who recovers in ten
            # seconds and a support ticket. PravaApiError/WalletError expose
            # `.code`; anything else simply has none to give.
            payload: dict = {"message": str(e)}
            code = getattr(e, "code", None)
            if isinstance(code, str) and code:
                payload["code"] = code
            await stream.emit_raw("run.error", payload)
        finally:
            await stream.close()

    task = asyncio.create_task(run())

    async def body():
        try:
            async for frame in stream.drain():
                yield frame
        finally:
            # Always tear down: signal cancel (which the poll loop observes and
            # returns from), delete the gate row so the table can't accumulate
            # and a late /approve finds nothing, then ensure the background task
            # is finished so nothing leaks.
            cancel.set()
            try:
                await _delete_approval(scope, run_id)
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


class ApproveRequest(BaseModel):
    approved: bool = True
    reason: str | None = None


@app.post("/api/errand/{run_id}/approve")
async def approve_errand(
    run_id: str,
    req: ApproveRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # AUTH REQUIRED, AND SCOPED TO THE CALLER. The UPDATE is filtered by
    # scope=user.id, so a run_id belonging to someone else matches zero rows and
    # resolves nothing. Authenticating alone would not have been enough: it would
    # still have let any signed-in account approve a stranger's purchase with
    # nothing but a leaked run_id. That (scope=user.id) filter IS the
    # authorization. The response for "not yours" is deliberately identical to
    # "no such run"/"already resolved", so this cannot be used to probe which
    # runs exist.
    result = await session.execute(
        update(Approval)
        .where(
            Approval.scope == user.id,
            Approval.run_id == run_id,
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
