"""Deepgram Voice Agent relay + tool bridge.

Browser tokens are FORBIDDEN on our Deepgram key, so the BACKEND holds the
Deepgram Voice Agent WS (`wss://agent.deepgram.com/v1/agent/converse`) and
relays audio + events to the browser over our own WS. The browser never talks
to Deepgram directly.

Wire contract (docs/api-reference.md — "Errand Voice Relay + Tool Bridge"):

  Handshake: /api/voice/ws?ticket=... — the browser first POSTs the
  authenticated /api/voice/ticket and presents the one-shot ticket here. No
  ticket, no Deepgram connection (close 4401). See app/voice/tickets.py.

  Browser -> backend:
    - binary: mic PCM (linear16, 48 kHz mono), forwarded verbatim to Deepgram.
    - JSON:   {type:"start"} | {type:"stop"} |
              {type:"approve", run_id, approved, reason?}

  backend -> Browser:
    - binary: agent TTS PCM (linear16, 16 kHz), forwarded verbatim from Deepgram.
    - JSON events:
        voice.state           {state: listening|thinking|speaking|idle}
        voice.clear_audio     {} — barge-in: DROP all queued/scheduled TTS now.
                              Sent on UserStartedSpeaking, ahead of voice.state.
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
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

import websockets
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("errand.voice")

from app.brokers import build_brokers
from app.brokers.linkup import LinkupSearchBroker
from app.config import settings
from app.contracts import AuditEvent
from app.orchestrator.guards import ApprovalDecision
from app.orchestrator.run_errand import run_errand
from app.orchestrator.shop_decide import make_shop_decide
from app.voice.tickets import redeem_ticket

DEEPGRAM_AGENT_URL = "wss://agent.deepgram.com/v1/agent/converse"

# Application-level "unauthorized" close code (the 4000-4999 range is reserved
# for the application). Deliberately NOT 1008/policy-violation: the browser must
# be able to tell "your ticket was missing/stale, sign in again" apart from any
# other policy close, and 1008 is indistinguishable from a generic refusal.
WS_UNAUTHORIZED = 4401

# ?model=sol|terra|luna. Retained because the browser still sends it and the chat
# path still uses it, but see VOICE_THINK_MODEL: the VOICE think model is no
# longer selected by it.
_MODEL_MAP = {
    "sol": "gpt-5.6-sol",
    "terra": "gpt-5.6-terra",
    "luna": "gpt-5.6-luna",
}
_DEFAULT_MODEL = "sol"

# ── Voice provider contract (doc-verified; do not extrapolate) ────────────────
# THINK: Deepgram-managed Anthropic. `claude-sonnet-5` is listed verbatim in
# Deepgram's supported Anthropic model table (Advanced tier). Because it is
# MANAGED, `agent.think.endpoint` is omitted and no Anthropic key is needed.
# Consequence, stated plainly: the sol/terra/luna selector no longer changes the
# voice LLM — voice always thinks with claude-sonnet-5, while the TEXT chat path
# (app/routers/chat.py) still honours the selector against gpt-5.6-*. Deepgram's
# managed Anthropic list does not contain the gpt-5.6 family, so one selector
# cannot address both.
# https://developers.deepgram.com/docs/voice-agent-llm-models
VOICE_THINK_PROVIDER = "anthropic"
VOICE_THINK_MODEL = "claude-sonnet-5"

# SPEAK: Deepgram-managed Cartesia. `sonic-2` and the voice id below are the
# values in Deepgram's own managed-Cartesia example. Cartesia is keyed by
# `model_id` + a {mode,id} voice object, NOT the `model` string Deepgram/OpenAI
# use. `speed` accepts slowest|slow|normal|fast|fastest or a number.
# https://developers.deepgram.com/docs/voice-agent-tts-models
VOICE_SPEAK_PROVIDER = "cartesia"
VOICE_SPEAK_MODEL_ID = "sonic-2"
VOICE_SPEAK_VOICE_ID = "a167e0f3-df7e-4d52-a9c3-f949145efdab"

# Human-in-the-loop gate timeout (seconds) for a voice-driven errand.
APPROVAL_TIMEOUT_S = 300

# ── Barge-in ──────────────────────────────────────────────────────────────────
# Deepgram's message-flow reference states the client obligation on
# UserStartedSpeaking plainly: "User began talking. Stop any audio playback
# immediately to handle barge-in."
#
# The relay cannot do that itself — the audio it already forwarded is sitting in
# the browser's Web Audio schedule, seconds of it, and a state change to
# "listening" does not unschedule anything. So the browser is told explicitly,
# and useVoiceAgent stops every scheduled source and resets its play cursor.
# Without this the agent talks straight over the person interrupting it, which
# is the single most conspicuous way a voice agent feels broken.
# https://developers.deepgram.com/docs/voice-agent-message-flow
CLEAR_AUDIO_EVENT = "voice.clear_audio"

# ── Progress narration during a long tool call ────────────────────────────────
# run_errand takes minutes: policy lookup, catalog search, one real browser quote
# per merchant attempted. Deepgram's think model is blocked on our
# FunctionCallResponse for that entire time, so the agent says NOTHING — the user
# is left listening to silence wondering whether it crashed.
#
# InjectAgentMessage is the documented way to make the agent speak mid-turn:
#   {"type":"InjectAgentMessage","message":"…","behavior":"default"|"queue"|"interrupt"}
# `queue` appends after any queued content without cutting off the current turn,
# which is what a progress note wants. If it arrives while the USER is mid-turn
# the server ignores it and answers InjectionRefused — harmless, and handled.
# https://developers.deepgram.com/docs/voice-agent-inject-agent-message
#
# Only these steps are narrated. Everything else in the audit stream is for the
# screen, not the ear: reading twelve events aloud is worse than silence.
NARRATED_STEPS: dict[str, str] = {
    "context.loaded": "Got your spend policy. Finding what to buy now.",
    "cart.merchant_unavailable": "That vendor doesn't have it. Trying the next one.",
    "cart.merchant_discovered": "None of your approved vendors had it, so I found another.",
    "cart.built": "Cart's ready. Pricing it up for your approval.",
}

# Floor between two spoken progress notes. A merchant ladder can emit several
# `cart.merchant_unavailable` events in a few seconds; narrating each one turns a
# helpful nudge into chatter.
NARRATION_MIN_GAP_S = 12.0

# Deepgram closes the socket at 1011 when it has received no Binary or Text
# frame from us for 10s. That close is NET-0001 ("The service has not received a
# Binary or Text frame from the client within the timeout window"); NET-0002 is a
# different one — no AUDIO inside the no-audio window — which a KeepAlive, being a
# text frame, does not answer, so the code cited here before named a failure this
# loop has no effect on. The Voice Agent surface reports the same condition as
# CLIENT_MESSAGE_TIMEOUT rather than a NET code.
# https://developers.deepgram.com/docs/stt-troubleshooting-websocket-data-and-net-errors
# https://developers.deepgram.com/docs/voice-agent-errors-warnings
#
# Deepgram asks for one {"type":"KeepAlive"} every 8s while idle. We poll every
# second and fire once the mic has been quiet for KEEPALIVE_AFTER_SILENCE_S,
# which leaves margin under both the 8s cadence and the 10s close.
# https://developers.deepgram.com/docs/agent-keep-alive
KEEPALIVE_POLL_S = 1.0
KEEPALIVE_AFTER_SILENCE_S = 5.0

# Deepgram retires every Voice Agent session at two hours and KeepAlive does not
# move it: "KeepAlive does not extend the maximum session length of 2 hours. The
# server closes every session at the 2-hour mark, however much traffic it has
# seen." It announces the ceiling itself — Warning/
# MAXIMUM_SESSION_LENGTH_APPROACHING at 1h55m, then Error/
# MAXIMUM_SESSION_LENGTH_REACHED at 2h — and both are forwarded to the browser
# below. This local ceiling is the backstop for when neither announcement lands:
# the alternative is a bare socket drop the browser can only render as an
# unexplained hiccup two hours into a conversation.
# https://developers.deepgram.com/docs/agent-keep-alive
# https://developers.deepgram.com/docs/voice-agent-errors-warnings
MAX_SESSION_S = 2 * 60 * 60

SYSTEM_PROMPT = (
    "You are Errand, a warm, concise voice concierge that runs real purchasing "
    "errands and answers questions grounded in live web results. "
    "You have two tools:\n"
    "- run_errand: hands a shopping/purchase task to the errand agent. Use it "
    "whenever the user wants something bought, ordered, or restocked. Pass the "
    "user's request verbatim as `intent`, and set `profile` to 'business' for "
    "work/office purchases or 'personal' for the user's own groceries/items. "
    "The errand PAUSES for approval before any spend, and that approval happens "
    "ON SCREEN with the user's passkey — a spoken 'yes' cannot authorise it and "
    "you must never imply otherwise. When the errand pauses, say what will be "
    "charged and to whom, then ask the user to confirm it on screen. Never invent "
    "an order confirmation; only report what the tool returns.\n"
    "- web_search: look up current facts, prices, or product recommendations. "
    "Summarize the grounded answer naturally; do not read raw URLs aloud.\n"
    "ANSWER, DON'T INTERROGATE. When the user asks something, go and find out — "
    "search rather than hedging or handing the question back. If a request is "
    "slightly underspecified, make the sensible assumption, say it in one "
    "clause, and answer. Ask only when the readings genuinely differ and you "
    "cannot pick, or when money is about to move. Never ask what you could have "
    "looked up. Don't quiz the user about budget or merchant before an errand — "
    "the spend policy supplies both.\n"
    "Speak in short, natural sentences. Avoid lists and filler. If something "
    "fails, say specifically what failed and what would fix it."
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
                            "'fast' for a sub-second answer to a simple, focused "
                            "question, 'standard' for agentic search, 'deep' for "
                            "several agentic iterations. Default 'standard'."
                        ),
                        # Mirrors LinkupSearchBroker.DEPTHS; see the citation there.
                        "enum": list(LinkupSearchBroker.DEPTHS),
                    },
                },
                "required": ["query"],
            },
        },
    ] + (
        [
            {
                "name": "shop_live",
                "description": (
                    "Shop a real store in a live browser and hand it to the user "
                    "to log in and PAY THEMSELVES on screen. Use when the user "
                    "wants to buy from a specific real store (especially one "
                    "needing a login) rather than the policy errand. The agent "
                    "fills the cart; the user completes payment in the live "
                    "browser — the agent never enters card details. Tell the user "
                    "to look at the screen and pay there."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "merchant_url": {"type": "string", "description": "The store URL to shop."},
                        "intent": {"type": "string", "description": "What to buy, in the user's words."},
                    },
                    "required": ["merchant_url", "intent"],
                },
            }
        ]
        if settings.live_handoff_ready
        else []
    )


def _settings_message(model_id: str) -> dict:
    """The first message on the Deepgram WS.

    THINK is Deepgram-managed Anthropic; SPEAK is Deepgram-managed Cartesia.
    `model_id` is accepted for signature compatibility with the chat path's
    sol/terra/luna selector but is deliberately NOT sent: see VOICE_THINK_MODEL.
    """
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
                "provider": {
                    "type": VOICE_THINK_PROVIDER,
                    "model": VOICE_THINK_MODEL,
                    # NOTE: `reasoning_mode` is deliberately ABSENT.
                    # Deepgram documents it as OpenAI-only ("Only supported with
                    # OpenAI reasoning models") and its accepted values are
                    # low|medium|high — so the "none" this used to send was not
                    # even in the documented enum, and it is meaningless for an
                    # anthropic provider. Sending an unknown provider field is
                    # the crash-class 4xx this repo has already been bitten by.
                    # https://developers.deepgram.com/docs/configure-voice-agent
                },
                # NOTE: no `endpoint`. Deepgram documents `agent.think.endpoint`
                # as OPTIONAL for `anthropic` because it provides a managed
                # Anthropic LLM ("For open_ai, anthropic, google, and nvidia, the
                # endpoint field is optional because Deepgram provides managed
                # LLMs"). Omitting it means Deepgram bills/authenticates the LLM
                # hop, so no Anthropic API key is required on our side.
                # https://developers.deepgram.com/docs/voice-agent-llm-models
                "prompt": SYSTEM_PROMPT,
                "functions": _think_functions(),
            },
            "speak": {
                "provider": {
                    "type": VOICE_SPEAK_PROVIDER,
                    # Cartesia takes `model_id` (NOT `model`, which is the
                    # Deepgram/OpenAI field) plus a voice object.
                    "model_id": VOICE_SPEAK_MODEL_ID,
                    "voice": {"mode": "id", "id": VOICE_SPEAK_VOICE_ID},
                    "speed": "normal",
                }
                # NOTE: no `endpoint`. This is the DEEPGRAM-MANAGED Cartesia form
                # from the "Deepgram-managed Cartesia TTS models" section, whose
                # own example carries no endpoint and no x-api-key — Cartesia is
                # billed through Deepgram's Standard tier. The BYO Cartesia form
                # lower on that page requires endpoint + CARTESIA_API_KEY, which
                # we do not hold.
                # https://developers.deepgram.com/docs/voice-agent-tts-models
            },
            "greeting": GREETING,
        },
    }


class VoiceSession:
    """One browser <-> backend <-> Deepgram bridge for a single connection."""

    def __init__(
        self,
        browser: WebSocket,
        model_key: str,
        profile: str,
        user_id: str,
        user_email: str,
    ) -> None:
        self._browser = browser
        self._model_id = _MODEL_MAP.get(model_key, _MODEL_MAP[_DEFAULT_MODEL])
        self._profile = profile if profile in ("business", "personal") else "business"
        # Who is spending. Taken from the redeemed ticket, never from the query
        # string, so a caller cannot attribute a purchase to someone else.
        self._user_id = user_id
        self._user_email = user_email
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
        # When this bridge started, measured against MAX_SESSION_S. Set here
        # rather than at connect so the clock covers the dial too — Deepgram's
        # two hours run from its side of the socket, and starting ours later
        # would let the local backstop trail the real ceiling.
        self._started_at = time.monotonic()
        # When we last made the agent speak a progress note, so a burst of audit
        # events cannot turn into a burst of speech. See NARRATION_MIN_GAP_S.
        self._last_narration_ts = 0.0

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

    async def _inject(self, message: str, *, behavior: str = "queue") -> bool:
        """Have the agent speak `message` mid-turn (Deepgram InjectAgentMessage).

        Returns whether it was sent, not whether it was spoken: the server may
        still answer InjectionRefused if the user turns out to be mid-turn, which
        is fine — a progress note is worth exactly nothing if it would talk over
        the person it is for.
        """
        if self._dg is None or self._closed.is_set():
            return False
        try:
            await self._to_deepgram_json(
                {"type": "InjectAgentMessage", "message": message, "behavior": behavior}
            )
            return True
        except Exception as e:  # noqa: BLE001 — narration must never break a run
            logger.info("InjectAgentMessage failed (non-fatal): %r", e)
            return False

    async def _narrate(self, step: str) -> None:
        """Speak a progress note for `step`, if it is one we narrate and we have
        not just spoken one."""
        line = NARRATED_STEPS.get(step)
        if line is None:
            return
        now = time.monotonic()
        if now - self._last_narration_ts < NARRATION_MIN_GAP_S:
            return
        self._last_narration_ts = now
        await self._inject(line)

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
        """Hold the socket open across audio gaps with a KeepAlive, and retire the
        session at Deepgram's two-hour ceiling with a stated cause. Runs until the
        session closes."""
        while not self._closed.is_set():
            try:
                await asyncio.sleep(KEEPALIVE_POLL_S)
            except asyncio.CancelledError:
                break
            if self._closed.is_set() or self._dg is None:
                break
            if time.monotonic() - self._started_at >= MAX_SESSION_S:
                # Emit BEFORE closing: _to_browser drops anything sent after
                # _closed is set, so the order here is what makes the cause
                # reach the user instead of a silent teardown.
                await self._to_browser(
                    {
                        "type": "voice.error",
                        "code": "MAXIMUM_SESSION_LENGTH_REACHED",
                        "message": (
                            "This voice session hit Deepgram's two-hour limit and "
                            "has ended. Start a new session to keep going."
                        ),
                    }
                )
                self._closed.set()
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
            # Deepgram: "Stop any audio playback immediately to handle barge-in."
            # The state change alone does not do that — seconds of agent audio are
            # already scheduled in the browser. Order matters: clear first, so the
            # talking stops before the orb changes to match.
            await self._to_browser({"type": CLEAR_AUDIO_EVENT})
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
        elif etype == "InjectionRefused":
            # We asked the agent to speak a progress note while the user was
            # mid-turn. Declining is correct behaviour, not a fault: the note
            # exists to reassure the user, and talking over them does the
            # opposite. Nothing to surface.
            # https://developers.deepgram.com/docs/voice-agent-inject-agent-message
            logger.debug("progress narration refused (user was speaking)")
        elif etype == "Warning":
            # Deepgram's advance notice, carrying MAXIMUM_SESSION_LENGTH_APPROACHING
            # at 1h55m. Forwarded so a long conversation can be told it is about to
            # be retired while it can still act on that, rather than narrating the
            # drop five minutes later.
            # https://developers.deepgram.com/docs/voice-agent-errors-warnings
            await self._to_browser(
                {
                    "type": "voice.warning",
                    "code": event.get("code") or "",
                    "message": event.get("description") or "Deepgram warning",
                }
            )
        # Welcome / other events: ignored (audio still flows).
        elif etype == "Error":
            # `code` rides along so MAXIMUM_SESSION_LENGTH_REACHED is separable
            # from a generic failure by the browser and by our own logs — the
            # description alone reads like every other error.
            await self._to_browser(
                {
                    "type": "voice.error",
                    "code": event.get("code") or "",
                    "message": event.get("description") or "Deepgram error",
                }
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
            elif name == "shop_live":
                content = await self._tool_shop_live(args)
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
            # The screen gets every event; the ear gets the few that explain a
            # long silence. Deepgram is blocked on our FunctionCallResponse for
            # the whole errand, so without this the agent simply goes quiet for
            # minutes.
            await self._narrate(ev.step)

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
            # Say the number out loud and point at the gate. `interrupt` rather
            # than `queue`: this is the one moment in the errand where being
            # heard immediately matters more than conversational manners, and
            # everything downstream is blocked on the user acting on it.
            await self._inject(_approval_line(payload), behavior="interrupt")
            self._last_narration_ts = time.monotonic()
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

        # Live browser view over voice, identical to the chat path: throttled JPEG
        # frames become browser.frame; image-less steps (the wallet path) become
        # audit lines. The browser reducer is shared, so the voice thread renders
        # the SAME shop cards + live screenshots as a typed errand.
        import base64

        last_frame_at = 0.0

        async def on_frame(step: str, detail: str, shot: bytes | None) -> None:
            nonlocal last_frame_at
            if not shot:
                await emit(AuditEvent(at=_now_iso(), step=step, detail=detail, data={}))
                return
            now = time.monotonic()
            if now - last_frame_at < 0.5:
                return
            last_frame_at = now
            await self._to_browser({
                "type": "browser.frame", "run_id": run_id, "mime": "image/jpeg",
                "b64": base64.b64encode(shot).decode("ascii"), "caption": detail,
            })

        shop_decide = make_shop_decide(self._model_id, reasoning_effort="none")

        await self._to_browser({"type": "run.started", "run_id": run_id, "model": self._model_id})
        try:
            outcome = await run_errand(
                brokers,
                profile=profile,  # type: ignore[arg-type]
                intent=intent,
                user_id=self._user_id,
                user_email_fallback=self._user_email,
                emit=emit,
                approve=approve,
                shop_decide=shop_decide,
                on_frame=on_frame,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # The provider CODE rides along here too: the voice agent reads the
            # summary aloud, and "binding limit reached" is a different sentence
            # from "card verification failed".
            err: dict = {"type": "run.error", "run_id": run_id, "message": str(e)}
            code = getattr(e, "code", None)
            if isinstance(code, str) and code:
                err["code"] = code
            await self._to_browser(err)
            return f"The errand failed: {e}"
        finally:
            self._approvals.pop(run_id, None)

        outcome = {**outcome, "run_id": run_id}
        await self._to_browser({"type": "run.done", **outcome})
        return _summarize_outcome(outcome)

    async def _tool_shop_live(self, args: dict) -> str:
        """Voice twin of the chat shop_live tool: the agent shops a real store in
        a live browser and the USER pays on screen. Same wire as chat — browser
        frames + a browser.liveview URL to the shared reducer — and the human's
        'done paying' arrives on the SAME {type:"approve"} control message the
        spend gate already uses. The agent narrates 'pay on screen' because a
        spoken 'yes' cannot complete someone else's checkout form."""
        merchant_url = (args.get("merchant_url") or "").strip()
        intent = (args.get("intent") or "").strip()
        if not merchant_url or not intent:
            return "I need both a store and what to buy."
        if not settings.live_handoff_ready:
            return "The live browser handoff isn't enabled here."

        run_id = uuid.uuid4().hex
        import base64

        from app.brokers.shopper import CloudflareShopperBroker, ShopperError
        from app.contracts import PurchaseContext

        async def emit(ev: AuditEvent) -> None:
            payload = ev.model_dump()
            payload["type"] = ev.step
            payload["run_id"] = run_id
            await self._to_browser(payload)
            await self._narrate(ev.step)

        last_frame_at = 0.0

        async def on_frame(step: str, detail: str, shot: bytes | None) -> None:
            nonlocal last_frame_at
            if not shot:
                await emit(AuditEvent(at=_now_iso(), step=step, detail=detail, data={}))
                return
            now = time.monotonic()
            if now - last_frame_at < 0.5:
                return
            last_frame_at = now
            await self._to_browser({
                "type": "browser.frame", "run_id": run_id, "mime": "image/jpeg",
                "b64": base64.b64encode(shot).decode("ascii"), "caption": detail,
            })

        async def on_live_view(url: str) -> None:
            await self._to_browser({"type": "browser.liveview", "run_id": run_id, "url": url})
            # Point the EAR at the screen — the payment happens there, not by voice.
            await self._inject(
                "I've opened the store in the live browser on your screen. "
                "Please log in if needed and complete the payment there, then tell me when you're done.",
                behavior="interrupt",
            )

        async def wait_for_human() -> dict:
            # The '{type:"approve"}' control message is the 'done paying' signal.
            fut: asyncio.Future[ApprovalDecision] = asyncio.get_running_loop().create_future()
            self._approvals[run_id] = fut
            try:
                decision = await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT_S)
            except asyncio.TimeoutError:
                return {"approved": False}
            finally:
                self._approvals.pop(run_id, None)
            return {"approved": decision.approved}

        shop_decide = make_shop_decide(self._model_id, reasoning_effort="none")
        ctx = PurchaseContext(profile=self._profile, approved_merchants=[], budget_cents=10**9, rules=[])

        await self._to_browser({"type": "run.started", "run_id": run_id, "model": self._model_id})
        try:
            shopper = CloudflareShopperBroker()
            order = await shopper.shop_live_handoff(
                merchant_url, intent, ctx,
                decide=shop_decide, wait_for_human=wait_for_human,
                on_frame=on_frame, on_live_view=on_live_view,
            )
        except asyncio.CancelledError:
            raise
        except ShopperError as e:
            await self._to_browser({"type": "run.error", "run_id": run_id, "message": str(e)})
            return f"The live checkout didn't complete: {e}"
        except Exception as e:  # noqa: BLE001
            await self._to_browser({"type": "run.error", "run_id": run_id, "message": str(e)})
            return f"The live checkout failed: {e}"
        finally:
            self._approvals.pop(run_id, None)

        outcome = {"run_id": run_id, "kind": "completed", "order_id": order.order_id}
        await self._to_browser({"type": "run.done", **outcome})
        return (
            f"You finished the checkout in the live browser. {order.confirmation_text}"
        )


def _approval_line(payload: dict) -> str:
    """What the agent says when the spend gate opens.

    Amount and merchant, then the on-screen instruction. It names the passkey
    because a spoken "yes" genuinely cannot authorise this — the gate resolves on
    a control message from the browser — and an agent that implies otherwise
    leaves the user waiting for a purchase that will time out.
    """
    cart = payload.get("cart") or {}
    total = cart.get("total_cents")
    merchant = ""
    checkout = cart.get("checkout") or {}
    url = checkout.get("merchant_url") or ""
    if url:
        merchant = url.split("://")[-1].split("/")[0]
    amount = f"${total / 100:.2f}" if isinstance(total, int) else "the cart total"
    where = f" at {merchant}" if merchant else ""
    return (
        f"That comes to {amount}{where}. Approve it on screen with your passkey "
        f"and I'll place the order."
    )


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
    """FastAPI WebSocket handler for
    `/api/voice/ws?model=sol&profile=business&ticket=...`.

    AUTH REQUIRED. The ticket is redeemed BEFORE anything expensive happens: no
    Deepgram socket is opened and run_errand is unreachable unless a live,
    unspent ticket names a real user. The handshake is accepted only to hand the
    browser a close CODE — a close sent before accept is turned into an opaque
    HTTP 403 by the ASGI server, which the browser reports as a generic 1006 and
    the hook cannot tell from a network blip. Accepting costs one socket for a
    few milliseconds and buys a diagnosable "sign in again".
    """
    ticket = redeem_ticket(websocket.query_params.get("ticket"))
    if ticket is None:
        await websocket.accept()
        await websocket.close(code=WS_UNAUTHORIZED, reason="voice ticket missing or expired")
        return

    model_key = websocket.query_params.get("model", _DEFAULT_MODEL)
    profile = websocket.query_params.get("profile", "business")
    await websocket.accept()
    session = VoiceSession(
        websocket,
        model_key,
        profile,
        # Same derivation as main.py's errand_stream, so a voice-driven errand
        # and a typed one attribute spend to the same identity.
        user_id=f"u_{ticket.user_id[:12]}",
        user_email=ticket.user_email,
    )
    try:
        await session.run()
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
