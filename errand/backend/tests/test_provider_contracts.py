"""Provider-contract regressions — the constants a provider doc dictates and our
own code kept drifting away from.

Every value asserted here was wrong in a shipped build at least once, and each
was a silent failure: a rejected tool call, a poll that ran out the clock, a
socket that dropped with no cause. The point of the file is that re-breaking one
of them fails here instead of in a live errand.

No network. The Prava cases drive the real broker against a stubbed
httpx.AsyncClient, so no request leaves the machine and no spend is possible.

Runs under pytest if it is installed, and standalone (`uv run python
tests/test_provider_contracts.py`) if it is not, since the backend does not
currently carry a test runner dependency.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.brokers import prava as prava_module  # noqa: E402
from app.brokers.linkup import LinkupSearchBroker  # noqa: E402
from app.brokers.prava import PravaPaymentBroker  # noqa: E402
from app.contracts import PollFailed, PollPending  # noqa: E402
from app.routers.chat import _TOOLS  # noqa: E402
from app.voice.relay import (  # noqa: E402
    KEEPALIVE_AFTER_SILENCE_S,
    MAX_SESSION_S,
    _settings_message,
    _think_functions,
)

# Not a credential: the broker rejects anything without the sk_ prefix, so the
# tests need a string that parses, and nothing here ever reaches the network.
_FAKE_SECRET = "sk_test_placeholder_never_sent"


# ── Deepgram Voice Agent Settings ────────────────────────────────────────────

def test_think_provider_sends_reasoning_mode_none() -> None:
    """gpt-5.6 rejects function-tool calls at any effective reasoning above
    "none" with an HTTP 400, and Deepgram forwards this field to the BYO endpoint
    as OpenAI's reasoning_effort. Dropping it fails at the LLM hop — which is the
    hop that decides whether run_errand gets called at all."""
    settings_msg = _settings_message("gpt-5.6-sol")
    think = settings_msg["agent"]["think"]
    assert think["provider"]["reasoning_mode"] == "none", think["provider"]


def test_think_endpoint_is_a_full_request_path() -> None:
    """Deepgram POSTs this URL verbatim rather than appending a route to it, so an
    API *base* here means every think request lands on a path that does not
    exist and the agent can hear but never answer."""
    url = _settings_message("gpt-5.6-sol")["agent"]["think"]["endpoint"]["url"]
    assert url.endswith("/chat/completions"), url


def test_session_ceiling_matches_the_documented_two_hours() -> None:
    """Deepgram closes every Voice Agent session at the 2-hour mark and KeepAlive
    does not move it. A local backstop above the real ceiling never fires and the
    drop goes back to being unexplained.
    https://developers.deepgram.com/docs/agent-keep-alive"""
    assert MAX_SESSION_S == 2 * 60 * 60


def test_keepalive_fires_inside_the_documented_window() -> None:
    """Deepgram asks for a KeepAlive every 8s while idle and closes at 1011 after
    10s with no frame at all, so the trigger has to sit under both."""
    assert KEEPALIVE_AFTER_SILENCE_S < 8.0
    assert KEEPALIVE_AFTER_SILENCE_S < 10.0


# ── Linkup depth allowlist ───────────────────────────────────────────────────

def test_linkup_depth_allowlist_is_exactly_the_documented_set() -> None:
    """Order and membership both matter: the tuple is rendered straight into two
    tool schemas, so an extra value teaches the model to send something Linkup
    4xxs on."""
    assert LinkupSearchBroker.DEPTHS == ("fast", "standard", "deep")


def _chat_web_search_depth_enum() -> list[str]:
    for tool in _TOOLS:
        fn = tool["function"]
        if fn["name"] == "web_search":
            return fn["parameters"]["properties"]["depth"]["enum"]
    raise AssertionError("chat exposes no web_search tool")


def _voice_web_search_depth_enum() -> list[str]:
    for fn in _think_functions():
        if fn["name"] == "web_search":
            return fn["parameters"]["properties"]["depth"]["enum"]
    raise AssertionError("voice exposes no web_search tool")


def test_both_tool_schemas_expose_the_same_depths() -> None:
    """Chat and voice reach the same broker. If one schema drifts, the same
    spoken question and typed question take different search paths and only one
    of them is reproducible."""
    chat = _chat_web_search_depth_enum()
    voice = _voice_web_search_depth_enum()
    assert set(chat) == set(voice) == set(LinkupSearchBroker.DEPTHS), (chat, voice)


# ── Prava poll_credential ────────────────────────────────────────────────────

class _StubResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(
                f"broker called raise_for_status on {self.status_code}; the "
                "status should have been handled before this point"
            )

    def json(self) -> dict:
        return self._payload


class _StubAsyncClient:
    """Stands in for httpx.AsyncClient. Pops one queued response per request, so
    a test spells out exactly what upstream says on each poll."""

    queue: list[_StubResponse] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_StubAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def get(self, url: str, **kwargs: object) -> _StubResponse:
        assert _StubAsyncClient.queue, f"unexpected extra request to {url}"
        return _StubAsyncClient.queue.pop(0)


def _poll(responses: list[_StubResponse], times: int = 1) -> list[object]:
    """Run poll_credential `times` against a queued set of upstream responses."""
    broker = PravaPaymentBroker(_FAKE_SECRET, "https://sandbox.api.prava.space")
    original = prava_module.httpx.AsyncClient
    _StubAsyncClient.queue = list(responses)
    prava_module.httpx.AsyncClient = _StubAsyncClient  # type: ignore[assignment]
    try:
        return [
            asyncio.run(broker.poll_credential("sess_test"))  # type: ignore[misc]
            for _ in range(times)
        ]
    finally:
        prava_module.httpx.AsyncClient = original  # type: ignore[assignment]
        _StubAsyncClient.queue = []


_AWAITING_RESULT_BODY = {
    "status": "awaiting_result",
    "transactions": [
        {
            "line_items": [
                {
                    "token": "tok_stubbed",
                    "dynamic_cvv": "123",
                    "expiry_month": "12",
                    "expiry_year": "2030",
                    "txn_ref_id": "txn_stubbed",
                }
            ]
        }
    ],
}


def test_credential_is_taken_at_awaiting_result() -> None:
    """The upstream reference documents token/dynamic_cvv as "Only present when
    status is awaiting_result" and its own 200 example returns them in that
    state. Keying on "completed" alone polls straight past the one status that
    carries the card, with no 4xx anywhere to explain the timeout.
    https://docs.prava.space/api-reference/get-payment-result"""
    [result] = _poll([_StubResponse(200, _AWAITING_RESULT_BODY)])
    assert getattr(result, "status", None) == "completed", result
    assert result.credential.token == "tok_stubbed"  # type: ignore[union-attr]
    assert result.credential.txn_ref_id == "txn_stubbed"  # type: ignore[union-attr]


def test_first_poll_404_fails_fast_instead_of_pending() -> None:
    """404 is "Session not found or doesn't belong to your merchant account" —
    a fact about the configured key that will not become false on the next poll.
    Reported as pending it costs the orchestrator's whole credential window and
    then blames Prava for what is a local misconfiguration."""
    [result] = _poll([_StubResponse(404)])
    assert isinstance(result, PollFailed), result
    assert result.code == "SESSION_NOT_FOUND"


def test_404_failure_leaks_no_key_or_upstream_text() -> None:
    """run_errand copies res.message into the client-facing `reason`, so whatever
    is written here is served to the browser."""
    [result] = _poll([_StubResponse(404)])
    assert isinstance(result, PollFailed)
    assert "sk_" not in result.message
    assert _FAKE_SECRET not in result.message


def test_404_after_a_seen_session_stays_pending() -> None:
    """Once a session has answered 200, a later 404 is likelier an upstream read
    inconsistency than a session that stopped existing, and the orchestrator's
    wall-clock still bounds the wait — so this one is absorbed rather than
    aborting a run that is about to hand over a real credential."""
    results = _poll([_StubResponse(200, {"status": "pending"}), _StubResponse(404)], times=2)
    assert isinstance(results[0], PollPending), results[0]
    assert isinstance(results[1], PollPending), results[1]


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
