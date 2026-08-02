"""Real-time SSE streaming. Client <-> server is streaming-only; the client
NEVER polls. Orchestrator events are pushed the instant they happen.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from app.contracts import AuditEvent


def sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class EventStream:
    """A bounded async queue that the orchestrator writes to and the SSE
    response drains. `emit` is passed to run_errand; `drain` yields frames."""

    def __init__(self) -> None:
        self._q: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def emit(self, event: AuditEvent) -> None:
        await self._q.put((event.step, event.model_dump()))

    async def emit_raw(self, event: str, data: dict) -> None:
        await self._q.put((event, data))

    async def close(self) -> None:
        await self._q.put(None)

    async def drain(self) -> AsyncIterator[str]:
        while True:
            item = await self._q.get()
            if item is None:
                break
            event, data = item
            yield sse_frame(event, data)
