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

def test_voice_thinks_with_deepgram_managed_anthropic() -> None:
    """The voice LLM is Deepgram-managed `anthropic` / `claude-sonnet-5`.

    `claude-sonnet-5` appears verbatim in Deepgram's supported Anthropic model
    table, and a model string outside a managed provider's list is rejected —
    which would leave the agent able to hear but never answer.
    https://developers.deepgram.com/docs/voice-agent-llm-models
    """
    provider = _settings_message("gpt-5.6-sol")["agent"]["think"]["provider"]
    assert provider["type"] == "anthropic", provider
    assert provider["model"] == "claude-sonnet-5", provider


def test_voice_think_omits_endpoint_and_openai_only_reasoning_mode() -> None:
    """Two absences, both load-bearing.

    `endpoint` is omitted because Deepgram documents it as optional for
    `anthropic` (it manages that LLM), so sending one would require an Anthropic
    key we do not hold. `reasoning_mode` is omitted because Deepgram documents it
    as OpenAI-only with values low|medium|high — the "none" this used to send was
    not in the documented enum at all, and an unknown provider field is the
    crash-class 4xx this repo has already been bitten by.
    https://developers.deepgram.com/docs/configure-voice-agent
    """
    think = _settings_message("gpt-5.6-sol")["agent"]["think"]
    assert "endpoint" not in think, think
    assert "reasoning_mode" not in think["provider"], think["provider"]


def test_voice_speaks_with_deepgram_managed_cartesia() -> None:
    """TTS is Deepgram-managed Cartesia, keyed by `model_id` + a {mode,id} voice.

    Cartesia does NOT take the `model` string that Deepgram/OpenAI use, and the
    managed form carries no endpoint (Cartesia is billed through Deepgram's
    Standard tier); the BYO form would need a Cartesia key we do not hold.
    https://developers.deepgram.com/docs/voice-agent-tts-models
    """
    speak = _settings_message("gpt-5.6-sol")["agent"]["speak"]
    provider = speak["provider"]
    assert provider["type"] == "cartesia", provider
    assert provider["model_id"] == "sonic-2", provider
    assert "model" not in provider, provider
    assert provider["voice"]["mode"] == "id", provider
    assert provider["voice"]["id"], provider
    assert "endpoint" not in speak, speak


def test_voice_think_model_is_not_driven_by_the_chat_selector() -> None:
    """Deepgram's managed Anthropic list contains no gpt-5.6 model, so the
    sol/terra/luna selector cannot address the voice LLM. Pinned so nobody
    "restores" the selector by feeding it into a provider that will reject it."""
    for key in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        provider = _settings_message(key)["agent"]["think"]["provider"]
        assert provider["model"] == "claude-sonnet-5", (key, provider)


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


# ── Prava merchant API: session creation, errors, saved cards ────────────────

class _RecordingClient:
    """Records the request and returns one queued response, for POST or GET."""

    queue: list[_StubResponse] = []
    sent: list[dict] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_RecordingClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, **kwargs: object) -> _StubResponse:
        _RecordingClient.sent.append({"url": url, **kwargs})
        return _RecordingClient.queue.pop(0)

    async def get(self, url: str, **kwargs: object) -> _StubResponse:
        _RecordingClient.sent.append({"url": url, **kwargs})
        return _RecordingClient.queue.pop(0)


def _with_recorder(responses: list[_StubResponse], fn):
    original = prava_module.httpx.AsyncClient
    _RecordingClient.queue = list(responses)
    _RecordingClient.sent = []
    prava_module.httpx.AsyncClient = _RecordingClient  # type: ignore[assignment]
    try:
        return fn(), list(_RecordingClient.sent)
    finally:
        prava_module.httpx.AsyncClient = original  # type: ignore[assignment]
        _RecordingClient.queue = []


_SESSION_OK = {
    "session_id": "ses_1",
    "session_token": "eyJhbGciOi",
    "iframe_url": "https://sandbox.collect.prava.space?session=ses_1",
    "order_id": "ord_1",
    "expires_at": "2026-08-02T18:05:07.664Z",
}


def test_create_session_keeps_the_ids_prava_returns() -> None:
    """order_id and expires_at were being dropped on the floor.

    Both are live sandbox values (verified via scripts/verify_prava_sandbox.py);
    order_id is the only handle for reconciling a Prava session against our run,
    and expires_at is how long the card page stays open."""
    from app.contracts import CartItem, CreateSessionInput, Merchant

    broker = PravaPaymentBroker(_FAKE_SECRET, "https://sandbox.api.prava.space")
    payload = CreateSessionInput(
        merchant=Merchant(name="Demo Pantry Co", url="https://demo-pantry.example.com"),
        total_cents=6300,
        user_id="u1",
        user_email="buyer@example.com",
        items=[CartItem(name="Beans", qty=2, price_cents=2100)],
        external_order_ref="errand-run-1",
    )
    result, sent = _with_recorder(
        [_StubResponse(201, _SESSION_OK)],
        lambda: asyncio.run(broker.create_session(payload)),
    )
    assert result.session_id == "ses_1"
    assert result.order_id == "ord_1"
    assert result.expires_at == "2026-08-02T18:05:07.664Z"
    assert result.session_token == "eyJhbGciOi"

    body = sent[0]["json"]
    assert body["total_amount"] == "63.00"
    assert body["external_order_ref"] == "errand-run-1"
    # The visible line must price at the TOTAL, not at a unit.
    #
    # This assertion used to require `quantity == 2`, which encoded the bug: Prava's
    # iframe renders only product_details[0] and shows its unit_price as the money
    # figure, so a $63.00 charge displayed "$21.00 · Qty: 2" on the very page where
    # the human approves it. See app/brokers/prava._product_details and
    # tests/test_prava_line_items.py.
    detail = body["purchase_context"][0]["product_details"][0]
    assert detail["quantity"] == 1
    assert detail["unit_price"] == body["total_amount"] == "63.00"
    assert "Beans" in detail["description"]


def test_create_session_omits_an_unusable_callback_url() -> None:
    """callback_url must be HTTPS. Sending a dev http:// one fails the whole
    session on an OPTIONAL field — so it is dropped rather than passed on."""
    from app.contracts import CartItem, CreateSessionInput, Merchant

    broker = PravaPaymentBroker(
        _FAKE_SECRET, "https://sandbox.api.prava.space", callback_url="http://localhost:3000/done"
    )
    _, sent = _with_recorder(
        [_StubResponse(201, _SESSION_OK)],
        lambda: asyncio.run(
            broker.create_session(
                CreateSessionInput(
                    merchant=Merchant(name="M", url="https://www.acme.com"),
                    total_cents=100,
                    user_id="u",
                    user_email="buyer@example.com",
                    items=[CartItem(name="x", qty=1, price_cents=100)],
                )
            )
        ),
    )
    assert "callback_url" not in sent[0]["json"]


def test_api_errors_carry_pravas_own_code_and_field_detail() -> None:
    """`raise_for_status()` renders a URL and a number. AUTH_1001 (bad key) and
    VAL_2001 (bad body, with the offending field) are different problems with
    different fixes, and the operator should not have to guess which."""
    from app.contracts import CartItem, CreateSessionInput, Merchant

    broker = PravaPaymentBroker(_FAKE_SECRET, "https://sandbox.api.prava.space")
    error_body = {
        "error": {
            "code": "VAL_2001",
            "message": "Invalid request body",
            "details": {"fieldErrors": {"currency": ["Must be 3 uppercase letters"]}},
        }
    }
    try:
        _with_recorder(
            [_StubResponse(400, error_body)],
            lambda: asyncio.run(
                broker.create_session(
                    CreateSessionInput(
                        merchant=Merchant(name="M", url="https://www.acme.com"),
                        total_cents=100,
                        user_id="u",
                        user_email="buyer@example.com",
                        items=[CartItem(name="x", qty=1, price_cents=100)],
                    )
                )
            ),
        )
    except prava_module.PravaApiError as exc:
        assert exc.code == "VAL_2001"
        assert exc.status == 400
        assert "currency" in str(exc)
    else:
        raise AssertionError("expected PravaApiError")


def test_list_cards_parses_the_documented_shape() -> None:
    broker = PravaPaymentBroker(_FAKE_SECRET, "https://sandbox.api.prava.space")
    body = {
        "cards": [
            {
                "card_id": "card_1",
                "card_last4": "1111",
                "card_brand": "VISA",
                "card_exp_month": 12,
                "card_exp_year": 26,
                "status": "active",
            },
            {"card_last4": "9999"},  # no card_id — unusable, must be dropped
        ],
        "count": 2,
    }
    cards, sent = _with_recorder(
        [_StubResponse(200, body)], lambda: asyncio.run(broker.list_cards("u1"))
    )
    assert [c.card_id for c in cards] == ["card_1"]
    assert cards[0].card_brand == "VISA"
    assert sent[0]["params"] == {"customer_id": "u1", "status": "active"}


def test_revoke_sends_a_body_because_the_server_rejects_an_empty_one() -> None:
    """Verified live: with Content-Type: application/json and no body the
    sandbox answers FST_ERR_CTP_EMPTY_JSON_BODY."""
    broker = PravaPaymentBroker(_FAKE_SECRET, "https://sandbox.api.prava.space")
    _, sent = _with_recorder(
        [_StubResponse(200, {"success": True})],
        lambda: asyncio.run(broker.revoke_session("ses_1")),
    )
    assert sent[0]["json"] == {}


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
