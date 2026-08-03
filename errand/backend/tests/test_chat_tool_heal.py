"""The chat path's tool-heal ladder: a rejected tool must not cost the turn.

THE FAILURE THIS PREVENTS. `tools` is sent whole on every request of the tool
loop, so ONE malformed function definition is an HTTP 400 on the entire request —
the user loses the answer, and every other tool goes with it, including the
built-in errand and search tools that had nothing to do with the bad server.

The ladder walks from least to most destructive: drop the one function the error
names, then the next, then every MCP tool, and only then give up. This drives the
REAL loop in app/routers/chat.py through a fake OpenAI client, because the part
worth pinning is the control flow — how many times it retries, what it drops each
time, and that a 400 which is NOT about tools is left alone rather than being
"healed" by throwing away capability that was never the problem.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conftest  # noqa: E402
from conftest import (  # noqa: E402
    api_client,
    ensure_schema,
    register_user,
    run_async,
    session_scope,
)

from app.models import McpServer, User  # noqa: E402
from app.routers import chat as chat_module  # noqa: E402


def _bad_request(message: str):
    """A BadRequestError shaped like the real one, without a live HTTP call."""
    import httpx
    from openai import BadRequestError

    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(400, request=request, json={"error": {"message": message}})
    return BadRequestError(message, response=response, body={"error": {"message": message}})


# The exact message the live API returns, from the probe in app/mcp/schema.py.
REAL_400 = (
    "Invalid schema for function 'mcp__Higgs__video_analysis_create': schema must "
    "have type 'object' and not have 'oneOf'/'anyOf'/'allOf'/'enum'/'const'/'not' "
    "at the top level."
)


# ── the predicates that decide whether to heal at all ────────────────────────


def test_only_tool_shaped_400s_trigger_the_heal() -> None:
    """A 400 about the CONVERSATION must not be "healed" by dropping tools.

    Retrying a context-length or bad-message error with fewer tools fails
    identically and burns the user's turn twice, so the classifier is deliberately
    narrow.
    """
    tool_shaped = [
        REAL_400,
        "Invalid schema for function 'x': bad",
        "Invalid 'tools[3].function.parameters': expected an object",
        "Invalid function parameters",
    ]
    for message in tool_shaped:
        assert chat_module._is_tool_schema_400(_bad_request(message)), message

    not_tool_shaped = [
        "This model's maximum context length is 128000 tokens.",
        "Invalid value for 'messages[2].role'",
        "Unsupported parameter: 'reasoning_effort'",
        "You exceeded your current quota.",
        "Invalid value for 'temperature'",
    ]
    for message in not_tool_shaped:
        assert not chat_module._is_tool_schema_400(_bad_request(message)), message


def test_the_blamed_tool_is_only_trusted_when_we_actually_sent_it() -> None:
    """The name is parsed out of prose, so it is checked against what we sent.

    Without that check a message naming a built-in tool, or a garbled name, would
    silently drop nothing and the ladder would spin on an unchanged tool set.
    """
    known = {"mcp__Higgs__video_analysis_create", "mcp__Acme__search"}
    assert (
        chat_module._blamed_tool(_bad_request(REAL_400), known)
        == "mcp__Higgs__video_analysis_create"
    )
    # Names a function we did not send: falls through to the broader rung.
    assert chat_module._blamed_tool(_bad_request(
        "Invalid schema for function 'something_else': bad"), known) is None
    # Names nothing at all.
    assert chat_module._blamed_tool(_bad_request("Invalid 'tools[0]'"), known) is None


# ── the ladder itself, through the real loop ─────────────────────────────────


class _FakeStream:
    """One streamed completion that yields plain text and no tool calls."""

    def __init__(self, text: str) -> None:
        self._text = text

    def __aiter__(self):
        async def gen():
            delta = type("D", (), {"content": self._text, "tool_calls": None})()
            choice = type("C", (), {"delta": delta})()
            yield type("Chunk", (), {"choices": [choice]})()

        return gen()


class _FakeCompletions:
    """Records every request, and fails the first `fail_times` with `error`."""

    def __init__(self, error: str, fail_times: int) -> None:
        self.error = error
        self.fail_times = fail_times
        self.calls: list[list[dict]] = []

    async def create(self, **kwargs):  # noqa: ANN003
        self.calls.append(list(kwargs.get("tools") or []))
        if len(self.calls) <= self.fail_times:
            raise _bad_request(self.error)
        return _FakeStream("here you go")


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = type("Chat", (), {"completions": completions})()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _install(completions: _FakeCompletions):
    original = chat_module._client
    chat_module._client = lambda: _FakeClient(completions)
    return original


async def _seed_user_with_tools(client, names: list[str]) -> tuple[str, dict]:
    """A registered user whose MCP server publishes `names`."""
    _, headers = await register_user(client)
    async with session_scope() as session:
        from sqlalchemy import select

        user = (await session.scalars(select(User).order_by(User.created_at.desc()))).first()
        session.add(
            McpServer(
                user_id=user.id,
                name="Higgs",
                config={"url": "https://example.com/mcp"},
                auth_mode="none",
                enabled=True,
                tools_json=[{"name": n, "description": n, "input_schema": {}} for n in names],
            )
        )
        await session.commit()
    return headers


def _mcp_names(tools: list[dict]) -> set[str]:
    return {
        t["function"]["name"]
        for t in tools
        if t.get("function", {}).get("name", "").startswith("mcp__")
    }


def _send(client, headers, text: str = "hello"):
    """POST a turn. The conversation is created lazily by this route, so a fresh
    uuid4 hex id is all it needs (see `_owned_or_created`)."""
    import uuid

    return client.post(
        f"/api/conversations/{uuid.uuid4().hex}/chat",
        json={"content": text, "profile": "business", "model": "sol"},
        headers=headers,
    )


def test_a_rejected_tool_is_dropped_and_the_turn_still_answers() -> None:
    """The higgsfield case: one named function is dropped, the rest survive, and
    the user gets their answer."""
    ensure_schema()

    async def scenario() -> None:
        completions = _FakeCompletions(REAL_400, fail_times=1)
        original = _install(completions)
        try:
            async with api_client() as client:
                headers = await _seed_user_with_tools(
                    client, ["video_analysis_create", "video_status", "list_videos"]
                )
                res = await _send(client, headers)
                assert res.status_code == 200, res.text
                body = res.text
        finally:
            chat_module._client = original

        assert len(completions.calls) == 2, "expected exactly one heal retry"

        first, second = completions.calls
        assert "mcp__Higgs__video_analysis_create" in _mcp_names(first)
        # Only the blamed tool went; its siblings on the SAME server stayed.
        assert "mcp__Higgs__video_analysis_create" not in _mcp_names(second)
        assert _mcp_names(second) == {"mcp__Higgs__video_status", "mcp__Higgs__list_videos"}
        # The built-in tools were never at risk.
        builtin = {t["function"]["name"] for t in second} - _mcp_names(second)
        assert "run_errand" in builtin and "web_search" in builtin
        # And the user actually got prose.
        assert "here you go" in body

    run_async(scenario())


def test_an_unnamed_rejection_drops_every_mcp_tool_but_keeps_the_builtins() -> None:
    """When the error names no function there is nothing finer to drop, so the
    whole MCP set goes — and the turn still answers with the built-in tools."""
    ensure_schema()

    async def scenario() -> None:
        completions = _FakeCompletions(
            "Invalid 'tools': one or more function definitions are invalid",
            fail_times=1,
        )
        original = _install(completions)
        try:
            async with api_client() as client:
                headers = await _seed_user_with_tools(client, ["a", "b"])
                res = await _send(client, headers)
                assert res.status_code == 200, res.text
        finally:
            chat_module._client = original

        assert len(completions.calls) == 2
        assert _mcp_names(completions.calls[1]) == set()
        builtin = {t["function"]["name"] for t in completions.calls[1]}
        assert "run_errand" in builtin

    run_async(scenario())


def test_the_ladder_walks_one_tool_at_a_time_then_gives_up_bounded() -> None:
    """A persistent rejection must terminate rather than spin.

    Each rung names a different tool, so the ladder drops them one by one and then
    stops at the cap — proving both that it makes progress and that it is bounded.
    """
    ensure_schema()

    class _Walking(_FakeCompletions):
        def __init__(self) -> None:
            super().__init__("", fail_times=99)
            self._order = ["a", "b", "c", "d"]

        async def create(self, **kwargs):  # noqa: ANN003
            tools = list(kwargs.get("tools") or [])
            self.calls.append(tools)
            present = _mcp_names(tools)
            for name in self._order:
                full = f"mcp__Higgs__{name}"
                if full in present:
                    raise _bad_request(
                        f"Invalid schema for function '{full}': bad at the top level."
                    )
            raise _bad_request("Invalid 'tools': still bad")

    completions = _Walking()
    original = _install(completions)

    async def scenario() -> None:
        try:
            async with api_client() as client:
                headers = await _seed_user_with_tools(client, ["a", "b", "c", "d"])
                res = await _send(client, headers)
                # The stream opens before the model is called, so a terminal
                # failure surfaces inside the SSE body rather than as a 4xx/5xx.
                assert res.status_code == 200, res.text
        finally:
            chat_module._client = original

    run_async(scenario())

    # 1 initial + MAX_TOOL_HEAL_ATTEMPTS retries, then it stops.
    assert len(completions.calls) == chat_module.MAX_TOOL_HEAL_ATTEMPTS + 1, (
        f"ladder made {len(completions.calls)} attempts"
    )
    # It shrank on every rung rather than re-sending the same set.
    sizes = [len(_mcp_names(c)) for c in completions.calls]
    assert sizes == sorted(sizes, reverse=True) and sizes[0] > sizes[-1], sizes


def test_a_non_tool_400_is_not_healed() -> None:
    """A context-length error must fail once, with the tools untouched.

    Healing it would throw away the user's integrations for a reason that has
    nothing to do with them, and fail anyway.
    """
    ensure_schema()

    async def scenario() -> None:
        completions = _FakeCompletions(
            "This model's maximum context length is 128000 tokens.", fail_times=99
        )
        original = _install(completions)
        try:
            async with api_client() as client:
                headers = await _seed_user_with_tools(client, ["a", "b"])
                res = await _send(client, headers)
                assert res.status_code == 200, res.text
        finally:
            chat_module._client = original

        assert len(completions.calls) == 1, "a non-tool 400 must not be retried"
        assert _mcp_names(completions.calls[0]) == {"mcp__Higgs__a", "mcp__Higgs__b"}

    run_async(scenario())


def test_a_user_with_no_mcp_servers_is_unaffected() -> None:
    """The heal must be inert when there is nothing to drop: a 400 with no MCP
    tools present is a real failure and belongs to the outer handler."""
    ensure_schema()

    async def scenario() -> None:
        completions = _FakeCompletions(REAL_400, fail_times=99)
        original = _install(completions)
        try:
            async with api_client() as client:
                _, headers = await register_user(client)
                res = await _send(client, headers)
                assert res.status_code == 200, res.text
        finally:
            chat_module._client = original

        assert len(completions.calls) == 1

    run_async(scenario())


def test_the_system_prompt_stops_naming_a_server_whose_tools_were_dropped() -> None:
    """The prompt names connected servers so the model reaches for them.

    After the ladder drops every MCP tool, leaving that sentence in place would
    tell the model it has capability it no longer has — which produces confident
    references to tools that are not in the request.
    """
    ensure_schema()

    class _Recording(_FakeCompletions):
        def __init__(self) -> None:
            super().__init__("Invalid 'tools': bad", fail_times=1)
            self.systems: list[str] = []

        async def create(self, **kwargs):  # noqa: ANN003
            messages = kwargs.get("messages") or []
            self.systems.append(messages[0].get("content", "") if messages else "")
            return await super().create(**kwargs)

    recording = _Recording()
    original = _install(recording)

    async def run() -> None:
        try:
            async with api_client() as client:
                headers = await _seed_user_with_tools(client, ["a"])
                res = await _send(client, headers)
                assert res.status_code == 200, res.text
        finally:
            chat_module._client = original

    run_async(run())

    assert len(recording.systems) == 2
    assert "Higgs" in recording.systems[0], "the first request should name the server"
    assert "Higgs" not in recording.systems[1], (
        "after dropping its tools the prompt must stop advertising the server"
    )


if __name__ == "__main__":
    raise SystemExit(conftest.run_standalone(dict(globals())))
