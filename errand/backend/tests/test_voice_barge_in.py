"""Voice agent: barge-in, progress narration, and the spend gate's spoken line.

No network. A VoiceSession is built against stub sockets and driven by feeding
it the Deepgram events it would receive.

Each case here is a documented provider obligation or a real silence the user
would otherwise sit through:

  * Barge-in — Deepgram's message flow says of UserStartedSpeaking: "User began
    talking. Stop any audio playback immediately to handle barge-in." Seconds of
    agent speech are already scheduled in the browser by then, so the relay must
    say so explicitly; a state change unschedules nothing.
  * Narration — the think model is blocked on our FunctionCallResponse for the
    whole errand, so a multi-minute run is dead air unless InjectAgentMessage
    fills it.
  * The approval line — a spoken "yes" cannot resolve the gate, and an agent
    that implies it can leaves the user waiting for a purchase that times out.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.voice import relay as relay_module  # noqa: E402
from app.voice.relay import (  # noqa: E402
    CLEAR_AUDIO_EVENT,
    NARRATED_STEPS,
    NARRATION_MIN_GAP_S,
    VoiceSession,
    _approval_line,
)


class _StubBrowser:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        import json

        self.sent.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append({"type": "__binary__", "len": len(data)})


class _StubDeepgram:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload) -> None:
        import json

        if isinstance(payload, (bytes, bytearray)):
            self.sent.append({"type": "__audio__"})
            return
        self.sent.append(json.loads(payload))


def _session() -> tuple[VoiceSession, _StubBrowser, _StubDeepgram]:
    browser = _StubBrowser()
    session = VoiceSession(browser, "sol", "business", "u_1", "u@test")  # type: ignore[arg-type]
    dg = _StubDeepgram()
    session._dg = dg  # type: ignore[assignment]
    return session, browser, dg


def _types(messages: list[dict]) -> list[str]:
    return [m.get("type", "") for m in messages]


# ── barge-in ──────────────────────────────────────────────────────────────────

def test_user_speaking_tells_the_browser_to_drop_queued_audio() -> None:
    session, browser, _ = _session()
    asyncio.run(session._handle_deepgram_event({"type": "UserStartedSpeaking"}))
    assert CLEAR_AUDIO_EVENT in _types(browser.sent), browser.sent


def test_the_clear_arrives_before_the_state_change() -> None:
    """Order is the whole point: stop the talking, then repaint the orb.

    Reversed, the UI says "listening" while the agent is still audibly speaking
    over the user — which looks like the state is lying.
    """
    session, browser, _ = _session()
    asyncio.run(session._handle_deepgram_event({"type": "UserStartedSpeaking"}))
    types = _types(browser.sent)
    assert types.index(CLEAR_AUDIO_EVENT) < types.index("voice.state")


def test_other_events_do_not_clear_audio() -> None:
    """A clear on the wrong event chops the agent off mid-word."""
    session, browser, _ = _session()
    for event in ("AgentStartedSpeaking", "AgentThinking", "SettingsApplied", "AgentAudioDone"):
        asyncio.run(session._handle_deepgram_event({"type": event}))
    assert CLEAR_AUDIO_EVENT not in _types(browser.sent)


# ── progress narration ────────────────────────────────────────────────────────

def test_narration_uses_the_documented_inject_shape() -> None:
    """{"type":"InjectAgentMessage","message":…,"behavior":…} — and `queue`, so a
    progress note appends rather than cutting off the current turn.
    https://developers.deepgram.com/docs/voice-agent-inject-agent-message"""
    session, _, dg = _session()
    asyncio.run(session._narrate("cart.built"))
    assert len(dg.sent) == 1
    sent = dg.sent[0]
    assert sent["type"] == "InjectAgentMessage"
    assert sent["behavior"] == "queue"
    assert sent["message"] == NARRATED_STEPS["cart.built"]
    assert set(sent) == {"type", "message", "behavior"}


def test_only_allowlisted_steps_are_spoken() -> None:
    """The screen gets all twelve audit events; reading them aloud is worse than
    silence."""
    session, _, dg = _session()
    for step in ("run.started", "payment.session", "payment.credential", "inbox.ready"):
        asyncio.run(session._narrate(step))
    assert dg.sent == []


def test_a_burst_of_events_does_not_become_a_burst_of_speech() -> None:
    """The merchant ladder can emit several 'unavailable' events in seconds."""
    session, _, dg = _session()
    for _ in range(4):
        asyncio.run(session._narrate("cart.merchant_unavailable"))
    assert len(dg.sent) == 1


def test_narration_resumes_once_the_gap_has_passed() -> None:
    session, _, dg = _session()
    asyncio.run(session._narrate("context.loaded"))
    session._last_narration_ts -= NARRATION_MIN_GAP_S + 1
    asyncio.run(session._narrate("cart.built"))
    assert len(dg.sent) == 2


def test_narration_never_breaks_the_errand() -> None:
    """A dead Deepgram socket must not take the run down with it — the errand is
    the point; the commentary is not."""

    class _Broken(_StubDeepgram):
        async def send(self, payload) -> None:
            raise RuntimeError("socket gone")

    session, _, _ = _session()
    session._dg = _Broken()  # type: ignore[assignment]
    assert asyncio.run(session._inject("hello")) is False


def test_injection_refused_is_not_surfaced_as_an_error() -> None:
    """Declining to talk over the user is correct behaviour, not a fault."""
    session, browser, _ = _session()
    asyncio.run(session._handle_deepgram_event({"type": "InjectionRefused"}))
    assert browser.sent == []


# ── the spend gate ────────────────────────────────────────────────────────────

def test_the_approval_line_states_the_amount_the_merchant_and_the_passkey() -> None:
    line = _approval_line(
        {
            "cart": {
                "total_cents": 2798,
                "checkout": {"merchant_url": "https://bonescoffee.com"},
            }
        }
    )
    assert "$27.98" in line
    assert "bonescoffee.com" in line
    # A spoken yes cannot resolve the gate; the line must not imply it can.
    assert "passkey" in line.lower()
    assert "on screen" in line.lower()


def test_the_approval_line_degrades_without_a_cart() -> None:
    """Never render "$None" or crash the gate over a missing field."""
    line = _approval_line({})
    assert "None" not in line
    assert "passkey" in line.lower()


def test_the_prompt_does_not_promise_spoken_approval() -> None:
    """The prompt used to say "wait for their yes/no" — for a gate that only
    resolves on a control message from the browser."""
    prompt = relay_module.SYSTEM_PROMPT.lower()
    assert "on screen" in prompt
    assert "passkey" in prompt


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{'FAILED' if failures else 'PASSED'} — {failures} failure(s)")
    sys.exit(1 if failures else 0)
