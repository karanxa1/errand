"""Deepgram Voice Agent relay + tool bridge.

Browser tokens are FORBIDDEN on our Deepgram key, so the BACKEND holds the
Deepgram Voice Agent WS (`wss://agent.deepgram.com/v1/agent/converse`) and
relays audio + events to the browser over our own WS. The browser never talks
to Deepgram directly.

Wire contract (docs/api-reference.md — "Errand Voice Relay + Tool Bridge"):

  Browser -> backend:
    - binary: mic PCM (linear16, 48 kHz mono), forwarded verbatim to Deepgram.
    - JSON:   {type:"start"} | {type:"stop"} |
              {type:"approve", run_id, approved, reason?}

  backend -> Browser:
    - binary: agent TTS PCM (linear16, 16 kHz), forwarded verbatim from Deepgram.
    - JSON events:
        voice.state           {state: listening|thinking|speaking|idle}
        voice.user_transcript {text, is_final}
        voice.agent_transcript{text}
        tool.call             {name, args}
        tool.result           {name, summary}
        websearch.result      {query, answer, sources:[{name,url,snippet}]}
        + every run_errand AuditEvent, emitted under its own step name
          (run.started, context.loaded, cart.built, approval.request, ...,
           run.done) so the chat thread renders the voice-driven errand.

Two think.functions are wired (both executed server-side here):
  1. run_errand(intent, profile?)  -> the existing orchestrator as a subagent.
  2. web_search(query, depth?)     -> Linkup grounded search.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import websockets
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("errand.voice")

from app.brokers import build_brokers
from app.brokers.linkup import LinkupSearchBroker
from app.config import settings
from app.contracts import AuditEvent
from app.orchestrator.guards import ApprovalDecision
from app.orchestrator.run_errand import run_errand

DEEPGRAM_AGENT_URL = "wss://agent.deepgram.com/v1/agent/converse"

# ?model=sol|terra|luna -> the BYO OpenAI think model id.
_MODEL_MAP = {
    "sol": "gpt-5.6-sol",
    "terra": "gpt-5.6-terra",
    "luna": "gpt-5.6-luna",
}
_DEFAULT_MODEL = "sol"

# Human-in-the-loop gate timeout (seconds) for a voice-driven errand.
APPROVAL_TIMEOUT_S = 300

# Deepgram closes an idle Voice Agent WS after ~10s of no audio (1011/NET-0002).
# Per docs/agent-keep-alive, send {"type":"KeepAlive"} at least every 8s during
# any audio gap. We poll every second and fire a KeepAlive once the mic has been
# silent for KEEPALIVE_AFTER_SILENCE_S — well under the 8s / 10s ceilings.
KEEPALIVE_POLL_S = 1.0
KEEPALIVE_AFTER_SILENCE_S = 5.0

SYSTEM_PROMPT = (
    "You are Errand, a warm, concise voice concierge that runs real purchasing "
    "errands and answers questions grounded in live web results. "
    "You have two tools:\n"
    "- run_errand: hands a shopping/purchase task to the errand agent. Use it "
    "whenever the user wants something bought, ordered, or restocked. Pass the "
    "user's request verbatim as `intent`, and set `profile` to 'business' for "
    "work/office purchases or 'personal' for the user's own groceries/items. "
    "The errand pauses for the user's spoken approval before any spend — when it "
    "does, tell the user what will be charged and to whom, and wait for their "
    "yes/no. Never invent an order confirmation; only report what the tool "
    "returns.\n"
    "- web_search: look up current facts, prices, or product recommendations. "
    "Summarize the grounded answer naturally; do not read raw URLs aloud.\n"
    "Speak in short, natural sentences. Avoid lists and filler. Confirm intent "
    "briefly before running an errand."
)

GREETING = "Hi, I'm Errand. I can run a purchase for you or look something up. What do you need?"


def _think_functions() -> list[dict]:
    """The two Voice Agent tools, in Deepgram converse function-definition form."""
    return [
        {
            "name": "run_errand",
            "description": (
                "Run a real purchasing errand end to end: load the spend policy, "
                "build a cart, create a payment session, PAUSE for the user's "
                "spoken approval, then check out and confirm. Use for any request "
                "to buy, order, or restock. Returns a short spoken summary "
                "(order id + total, or why it stopped)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": (
                            "The user's purchase request in their own words, e.g. "
                            "'restock the office pantry under $200, approved brands only'."
                        ),
                    },
                    "profile": {
                        "type": "string",
                        "description": (
                            "'business' for work/office purchases, 'personal' for "
                            "the user's own groceries/items. Default 'business'."
                        ),
                        "enum": ["business", "personal"],
                    },
                },
                "required": ["intent"],
            },
        },
        {
            "name": "web_search",
            "description": (
                "Search the live web for current facts, prices, or product "
                "recommendations and return a grounded answer with sources. Use "
                "whenever the user asks something you should verify against the web."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query in natural language.",
                    },
                    "depth": {
                        "type": "string",
                        "description": (
                            "'standard' for a fast answer, 'deep' for a "
                            "multi-iteration search. Default 'standard'."
                        ),
                        "enum": ["standard", "deep"],
                    },
                },
                "required": ["query"],
            },
        },
    ]


def _settings_message(model_id: str) -> dict:
    """The first message on the Deepgram WS. BYO OpenAI endpoint lets us use the
    gpt-5.6-{sol,terra,luna} models the managed list doesn't expose."""
    return {
        "type": "Settings",
        "audio": {
            "input": {"encoding": "linear16", "sample_rate": 48000},
            "output": {"encoding": "linear16", "sample_rate": 16000, "container": "none"},
        },
        "agent": {
            "language": "en",
            "listen": {"provider": {"type": "deepgram", "model": "nova-3"}},
            "think": {
                "provider": {"type": "open_ai", "model": model_id},
                "endpoint": {
                    "url": "https://api.openai.com/v1",
                    "headers": {"Authorization": f"Bearer {settings.openai_api_key}"},
                },
                "prompt": SYSTEM_PROMPT,
                "functions": _think_functions(),
            },
            "speak": {"provider": {"type": "deepgram", "model": "aura-2-thalia-en"}},
            "greeting": GREETING,
        },
    }


class VoiceSession:
    """One browser <-> backend <-> Deepgram bridge for a single connection."""

    def __init__(self, browser: WebSocket, model_key: str, profile: str) -> None:
        self._browser = browser
        self._model_id = _MODEL_MAP.get(model_key, _MODEL_MAP[_DEFAULT_MODEL])
        self._profile = profile if profile in ("business", "personal") else "business"
        self._dg: websockets.WebSocketClientProtocol | None = None  # type: ignore[name-defined]
        # Starlette/FastAPI WS sends are not concurrency-safe; serialize them.
        self._browser_lock = asyncio.Lock()
        self._dg_lock = asyncio.Lock()
        # Pending approval gates, keyed by run_id, resolved by the browser's
        # {type:"approve", run_id, approved} control message.
        self._approvals: dict[str, asyncio.Future[ApprovalDecision]] = {}
        # In-flight tool executions (run_errand can take minutes); tracked so we
        # can cancel cleanly on disconnect.
        self._tool_tasks: set[asyncio.Task] = set()
        self._closed = asyncio.Event()
        # Monotonic time of the last mic frame forwarded to Deepgram; the
        # keepalive loop uses this to detect audio gaps.
        self._last_audio_ts = time.monotonic()

    # ── browser I/O (serialized) ─────────────────────────────────────────────

    async def _to_browser(self, payload: dict) -> None:
        if self._closed.is_set():
            return
        async with self._browser_lock:
            try:
                await self._browser.send_text(json.dumps(payload))
            except Exception:
                self._closed.set()

    async def _audio_to_browser(self, data: bytes) -> None:
        if self._closed.is_set():
            return
        async with self._browser_lock:
            try:
                await self._browser.send_bytes(data)
            except Exception:
                self._closed.set()

    # ── deepgram I/O (serialized) ────────────────────────────────────────────

    async def _to_deepgram_json(self, payload: dict) -> None:
        if self._dg is None:
            return
        async with self._dg_lock:
            await self._dg.send(json.dumps(payload))

    async def _to_deepgram_audio(self, data: bytes) -> None:
        if self._dg is None:
            return
        self._last_audio_ts = time.monotonic()
        async with self._dg_lock:
            await self._dg.send(data)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Open the Deepgram WS, send Settings, then pump both directions until
        either side closes."""
        try:
            self._dg = await websockets.connect(
                DEEPGRAM_AGENT_URL,
                additional_headers={
                    "Authorization": f"Token {settings.deepgram_api_key}"
                },
                max_size=None,
                # Keep the socket alive at the transport layer, but never let the
                # library self-close on a slow pong: Deepgram can be slow to answer
                # protocol pings under heavy streaming or while a long tool call
                # runs, and a fired ping_timeout would drop the whole session
                # mid-conversation. Application-level KeepAlive (below) is what
                # actually satisfies Deepgram's idle timeout.
                ping_interval=20,
                ping_timeout=None,
                close_timeout=5,
            )
        except Exception as e:  # connect refused / auth / network
            await self._to_browser(
                {"type": "voice.error", "message": f"Deepgram connect failed: {e}"}
            )
            return

        try:
            await self._to_deepgram_json(_settings_message(self._model_id))
            await self._to_browser({"type": "voice.state", "state": "listening"})

            browser_task = asyncio.create_task(self._browser_reader())
            dg_task = asyncio.create_task(self._deepgram_reader())
            keepalive_task = asyncio.create_task(self._keepalive_loop())
            tasks = {browser_task, dg_task, keepalive_task}
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, (asyncio.CancelledError, WebSocketDisconnect)):
                    logger.warning("voice relay task ended with error: %r", exc)
                    await self._to_browser(
                        {"type": "voice.error", "message": f"relay error: {exc}"}
                    )
        finally:
            await self._shutdown()

    async def _keepalive_loop(self) -> None:
        """Send Deepgram a KeepAlive during audio gaps so its ~10s no-audio
        timeout (1011/NET-0002) never fires. Runs until the session closes."""
        while not self._closed.is_set():
            try:
                await asyncio.sleep(KEEPALIVE_POLL_S)
            except asyncio.CancelledError:
                break
            if self._closed.is_set() or self._dg is None:
                break
            gap = time.monotonic() - self._last_audio_ts
            if gap < KEEPALIVE_AFTER_SILENCE_S:
                continue
            try:
                await self._to_deepgram_json({"type": "KeepAlive"})
            except Exception as e:
                logger.warning("KeepAlive send failed: %r", e)
                break

    async def _shutdown(self) -> None:
        self._closed.set()
        # Unblock any pending approval so run_errand tasks can finish/abort.
        for run_id, fut in list(self._approvals.items()):
            if not fut.done():
                fut.set_result(
                    ApprovalDecision(approved=False, approval_id=run_id, reason="session closed")
                )
        for t in list(self._tool_tasks):
            if not t.done():
                t.cancel()
        if self._dg is not None:
            try:
                await asyncio.wait_for(self._dg.close(), timeout=5)
            except Exception:
                pass

    # ── browser -> backend ─────────────────────────────────────────────────────

    async def _browser_reader(self) -> None:
        """Forward mic audio to Deepgram; act on JSON control messages."""
        while not self._closed.is_set():
            try:
                message = await self._browser.receive()
            except WebSocketDisconnect:
                break
            except RuntimeError:
                break

            if message.get("type") == "websocket.disconnect":
                break

            data = message.get("bytes")
            if data is not None:
                await self._to_deepgram_audio(data)
                continue

            text = message.get("text")
            if text is None:
                continue
            try:
                control = json.loads(text)
            except json.JSONDecodeError:
                continue
            await self._handle_control(control)

    async def _handle_control(self, control: dict) -> None:
        ctype = control.get("type")
        if ctype == "start":
            # Deepgram already got Settings on connect; nothing else to do.
            return
        if ctype == "stop":
            self._closed.set()
            return
        if ctype == "approve":
            run_id = control.get("run_id")
            fut = self._approvals.get(run_id or "")
            if fut is not None and not fut.done():
                fut.set_result(
                    ApprovalDecision(
                        approved=bool(control.get("approved")),
                        approval_id=run_id or "",
                        reason=control.get("reason"),
                    )
                )
            return

    # ── deepgram -> backend ─────────────────────────────────────────────────────

    async def _deepgram_reader(self) -> None:
        assert self._dg is not None
        try:
            async for message in self._dg:
                if self._closed.is_set():
                    break
                if isinstance(message, bytes):
                    await self._audio_to_browser(message)
                    continue
                try:
                    event = json.loads(message)
                except json.JSONDecodeError:
                    continue
                await self._handle_deepgram_event(event)
        except websockets.exceptions.ConnectionClosed as e:
            # Deepgram closed the socket. Surface the real code/reason (e.g.
            # 1011 / NET-0002 no-audio timeout) so drops are diagnosable rather
            # than showing a vague "Voice hiccup".
            if not self._closed.is_set():
                code = getattr(e, "code", None)
                reason = getattr(e, "reason", "") or str(e)
                logger.warning("Deepgram WS closed: code=%s reason=%r", code, reason)
                await self._to_browser(
                    {
                        "type": "voice.error",
                        "message": f"Deepgram closed the connection (code {code}): {reason}",
                    }
                )

    async def _handle_deepgram_event(self, event: dict) -> None:
        etype = event.get("type")

        if etype == "SettingsApplied":
            await self._to_browser({"type": "voice.state", "state": "listening"})
        elif etype == "UserStartedSpeaking":
            await self._to_browser({"type": "voice.state", "state": "listening"})
        elif etype == "AgentThinking":
            await self._to_browser({"type": "voice.state", "state": "thinking"})
        elif etype == "AgentStartedSpeaking":
            await self._to_browser({"type": "voice.state", "state": "speaking"})
        elif etype == "AgentAudioDone":
            await self._to_browser({"type": "voice.state", "state": "idle"})
        elif etype == "ConversationText":
            role = event.get("role")
            content = event.get("content") or ""
            if role == "user":
                await self._to_browser(
                    {"type": "voice.user_transcript", "text": content, "is_final": True}
                )
            elif role == "assistant":
                await self._to_browser({"type": "voice.agent_transcript", "text": content})
        elif etype == "FunctionCallRequest":
            # May carry multiple calls; execute each. run_errand may take minutes,
            # so run in a background task and reply when it resolves — the reader
            # loop keeps pumping audio/events meanwhile.
            for call in event.get("functions") or []:
                task = asyncio.create_task(self._run_tool(call))
                self._tool_tasks.add(task)
                task.add_done_callback(self._tool_tasks.discard)
        # Welcome / Error / other events: ignored (audio still flows).
        elif etype == "Error":
            await self._to_browser(
                {"type": "voice.error", "message": event.get("description") or "Deepgram error"}
            )

    # ── tool bridge ─────────────────────────────────────────────────────────────

    async def _run_tool(self, call: dict) -> None:
        call_id = call.get("id")
        name = call.get("name") or ""
        raw_args = call.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except (json.JSONDecodeError, TypeError):
            args = {}

        await self._to_browser({"type": "tool.call", "name": name, "args": args})

        try:
            if name == "run_errand":
                content = await self._tool_run_errand(args)
            elif name == "web_search":
                content = await self._tool_web_search(args)
            else:
                content = f"Unknown tool: {name}"
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never let a tool crash the relay
            content = f"Tool {name} failed: {e}"

        await self._to_browser({"type": "tool.result", "name": name, "summary": content})
        await self._reply_function(call_id, name, content)

    async def _reply_function(self, call_id: Any, name: str, content: str) -> None:
        await self._to_deepgram_json(
            {
                "type": "FunctionCallResponse",
                "id": call_id,
                "name": name,
                "content": content,
            }
        )

    async def _tool_web_search(self, args: dict) -> str:
        query = (args.get("query") or "").strip()
        depth = args.get("depth") or "standard"
        if not query:
            return "No search query was provided."
        broker = LinkupSearchBroker(settings.linkup_api_key, settings.linkup_api_base)
        result = await broker.search(query, depth=depth)
        answer = result.get("answer") or ""
        sources = result.get("sources") or []
        await self._to_browser(
            {
                "type": "websearch.result",
                "query": query,
                "answer": answer,
                "sources": sources,
            }
        )
        titles = [s.get("name", "") for s in sources[:3] if s.get("name")]
        if titles:
            return f"{answer}\n\nSources: {', '.join(titles)}"
        return answer or "No results found."

    async def _tool_run_errand(self, args: dict) -> str:
        intent = (args.get("intent") or "").strip()
        profile = args.get("profile") or self._profile
        if profile not in ("business", "personal"):
            profile = "business"
        if not intent:
            return "I need to know what to buy before I can run the errand."

        run_id = uuid.uuid4().hex
        brokers = build_brokers()

        # Forward every orchestrator AuditEvent to the browser under its own step
        # name so the chat thread renders the voice-driven errand in real time.
        async def emit(ev: AuditEvent) -> None:
            payload = ev.model_dump()
            payload["type"] = ev.step
            payload["run_id"] = run_id
            await self._to_browser(payload)

        # Approval gate: surface approval.request to the browser, then block on
        # the browser's {type:"approve", run_id, approved} control message.
        async def approve(payload: dict) -> ApprovalDecision:
            fut: asyncio.Future[ApprovalDecision] = (
                asyncio.get_running_loop().create_future()
            )
            self._approvals[run_id] = fut
            await self._to_browser(
                {"type": "approval.request", "run_id": run_id, **payload}
            )
            try:
                decision = await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT_S)
            except asyncio.TimeoutError:
                await self._to_browser(
                    {
                        "type": "approval.timeout",
                        "run_id": run_id,
                        "timeout_s": APPROVAL_TIMEOUT_S,
                    }
                )
                return ApprovalDecision(approved=False, approval_id=run_id, timed_out=True)
            finally:
                self._approvals.pop(run_id, None)
            return ApprovalDecision(
                approved=decision.approved,
                approval_id=run_id,
                reason=decision.reason,
                timed_out=decision.timed_out,
            )

        await self._to_browser({"type": "run.started", "run_id": run_id, "model": self._model_id})
        try:
            outcome = await run_errand(
                brokers,
                profile=profile,  # type: ignore[arg-type]
                intent=intent,
                user_id="u_demo",
                user_email_fallback="operator@example.com",
                emit=emit,
                approve=approve,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._to_browser(
                {"type": "run.error", "run_id": run_id, "message": str(e)}
            )
            return f"The errand failed: {e}"
        finally:
            self._approvals.pop(run_id, None)

        outcome = {**outcome, "run_id": run_id}
        await self._to_browser({"type": "run.done", **outcome})
        return _summarize_outcome(outcome)


def _summarize_outcome(outcome: dict) -> str:
    """A short, speakable summary of the errand result for the agent to voice."""
    kind = outcome.get("kind")
    if kind == "completed":
        oid = outcome.get("order_id") or outcome.get("confirmation_order_id") or "?"
        total = outcome.get("total_cents")
        if isinstance(total, int):
            return f"Order {oid} placed for ${total / 100:.2f}."
        return f"Order {oid} placed."
    reason = outcome.get("reason") or "the errand stopped."
    if kind == "aborted":
        return f"I stopped the errand: {reason}"
    if kind == "failed":
        return f"The errand failed: {reason}"
    return reason


async def voice_ws(websocket: WebSocket) -> None:
    """FastAPI WebSocket handler for `/api/voice/ws?model=sol&profile=business`."""
    model_key = websocket.query_params.get("model", _DEFAULT_MODEL)
    profile = websocket.query_params.get("profile", "business")
    await websocket.accept()
    session = VoiceSession(websocket, model_key, profile)
    try:
        await session.run()
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
