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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.brokers import build_brokers
from app.config import settings
from app.orchestrator.run_errand import run_errand
from app.orchestrator.stream import EventStream

app = FastAPI(title="Errand Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory approval gates keyed by run id. Each run awaits its Future until the
# frontend POSTs /approve (after the operator confirms + passkey).
_approvals: dict[str, asyncio.Future[bool]] = {}


MODELS = [
    {"key": "sol", "label": "Sol", "tagline": "Flagship — most capable", "id": "gpt-5.6-sol"},
    {"key": "terra", "label": "Terra", "tagline": "Balanced — everyday", "id": "gpt-5.6-terra"},
    {"key": "luna", "label": "Luna", "tagline": "Fastest — lightweight", "id": "gpt-5.6-luna"},
]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


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
    stream = EventStream()
    brokers = build_brokers()

    fut: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
    _approvals[run_id] = fut

    async def approve(_payload: dict) -> bool:
        # Emit the approval request to the client, then block until /approve.
        await stream.emit_raw("approval.request", {"run_id": run_id, **_payload})
        try:
            return await asyncio.wait_for(fut, timeout=300)
        except asyncio.TimeoutError:
            return False

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
            )
            await stream.emit_raw("run.done", outcome)
        except Exception as e:  # surface errors as a stream event, never 500 mid-stream
            await stream.emit_raw("run.error", {"message": str(e)})
        finally:
            _approvals.pop(run_id, None)
            await stream.close()

    task = asyncio.create_task(run())

    async def body():
        async for frame in stream.drain():
            yield frame
        await task  # ensure cleanup

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


class ApproveRequest(BaseModel):
    approved: bool = True


@app.post("/api/errand/{run_id}/approve")
async def approve_errand(run_id: str, req: ApproveRequest) -> dict:
    fut = _approvals.get(run_id)
    if fut is None or fut.done():
        return {"ok": False, "reason": "no pending approval for this run"}
    fut.set_result(req.approved)
    return {"ok": True}
