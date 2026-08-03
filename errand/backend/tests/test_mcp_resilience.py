"""Healing: what happens when a server, or a tool definition, misbehaves.

The properties here are the ones that decide whether an integration FEELS
reliable, which is a different question from whether it is correct:

  * a transient failure is retried, because a suspended instance cold-starting is
    the single most common way a working server looks broken;
  * an ANSWER (401, an SSRF refusal, a 404) is never retried, because retrying it
    delays the thing the user actually needs;
  * a tool definition the model API rejects costs that ONE tool, then all MCP
    tools, but never the turn;
  * a stale cached catalogue corrects itself instead of failing identically for
    ever.

Every retry assertion here counts ATTEMPTS rather than asserting on timing, so the
tests do not depend on the backoff constants.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conftest  # noqa: E402
from conftest import ensure_schema, run_async, session_scope  # noqa: E402

import httpx2  # noqa: E402

from app.mcp import client as mcp_client  # noqa: E402
from app.mcp import registry as mcp_registry  # noqa: E402
from app.mcp.config import McpConfigError  # noqa: E402
from app.models import McpServer, User  # noqa: E402

# ── classification: what may be retried, and what must not be ────────────────


def test_transient_failures_are_retryable() -> None:
    """Each of these is a server that might answer if asked again."""
    request = httpx2.Request("POST", "https://example.com/mcp")
    cases = [
        httpx2.ConnectError("connection refused"),
        httpx2.ConnectTimeout("timed out"),
        httpx2.ReadTimeout("timed out"),
        httpx2.WriteTimeout("timed out"),
        httpx2.PoolTimeout("pool"),
        httpx2.RemoteProtocolError("server disconnected"),
        httpx2.ReadError("connection reset"),
        RuntimeError("upstream connect error: connection reset by peer"),
        RuntimeError("503 Service Unavailable"),
    ]
    for exc in cases:
        assert mcp_client.is_transient(exc), f"{exc!r} should be retryable"

    for status in (408, 429, 500, 502, 503, 504):
        exc = httpx2.HTTPStatusError(
            f"{status}", request=request, response=httpx2.Response(status, request=request)
        )
        assert mcp_client.is_transient(exc), f"HTTP {status} should be retryable"


def test_answers_are_never_retried() -> None:
    """A retry here would delay the real remedy, or repeat a security decision.

    The SSRF case is the sharpest: if a DNS record is flapping between a public and
    a private address, retrying a refusal is a second roll of the dice on a check
    that already said no.
    """
    request = httpx2.Request("POST", "https://example.com/mcp")
    never = [
        asyncio.CancelledError(),
        mcp_client.McpAuthRequired("authorize first"),
        McpConfigError("that host is not public"),
        mcp_client.McpConnectionError(
            "Refused a request to 'localhost': that host is not public"
        ),
    ]
    for exc in never:
        assert not mcp_client.is_transient(exc), f"{exc!r} must not be retried"

    for status in (400, 401, 403, 404, 405, 422):
        exc = httpx2.HTTPStatusError(
            f"{status}", request=request, response=httpx2.Response(status, request=request)
        )
        assert not mcp_client.is_transient(exc), f"HTTP {status} must not be retried"

    # A 401 recognised only from its text, not a status code.
    assert not mcp_client.is_transient(RuntimeError("401 unauthorized: invalid_token"))


def test_a_transient_failure_wrapped_in_a_task_group_is_still_recognised() -> None:
    """The SDK runs its transport in an anyio task group, so failures arrive
    wrapped — usually twice. A classifier that only inspects the outermost
    exception would call every real failure non-transient."""
    inner = httpx2.ConnectTimeout("timed out")
    wrapped = BaseExceptionGroup("unhandled errors in a TaskGroup", [inner])
    assert mcp_client.is_transient(wrapped)

    doubly = BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [inner])])
    assert mcp_client.is_transient(doubly)

    # And the same for a wrapped 401, which must stay non-retryable.
    auth = BaseExceptionGroup("g", [mcp_client.McpAuthRequired("nope")])
    assert not mcp_client.is_transient(auth)


def test_tool_missing_is_distinguished_from_a_transport_failure() -> None:
    """"The tool is gone" means the call REACHED the server and it answered."""
    missing = [
        RuntimeError("Unknown tool: do_thing"),
        RuntimeError("tool not found: do_thing"),
        RuntimeError("No such tool 'do_thing'"),
        RuntimeError("Invalid tool name do_thing"),
    ]
    for exc in missing:
        assert mcp_client.is_tool_missing(exc), f"{exc!r} should read as a missing tool"

    not_missing = [
        httpx2.ConnectError("connection refused"),
        RuntimeError("resource not found: /docs/x"),  # no "tool" in the text
        RuntimeError("timed out"),
    ]
    for exc in not_missing:
        assert not mcp_client.is_tool_missing(exc), f"{exc!r} is not a missing tool"


# ── the retry actually happens, and stops when it should ─────────────────────


async def _seed_server(name: str = "Flaky") -> tuple[str, str]:
    """A user with one enabled server carrying one cached tool."""
    async with session_scope() as session:
        user = User(email=f"{name.lower()}-{os.urandom(4).hex()}@example.com",
                    password_hash="x")
        session.add(user)
        await session.flush()
        server = McpServer(
            user_id=user.id,
            name=name,
            config={"url": "https://example.com/mcp"},
            auth_mode="none",
            enabled=True,
            tools_json=[{"name": "do_thing", "description": "d", "input_schema": {}}],
        )
        session.add(server)
        # COMMIT, not just flush: every function under test opens its OWN session
        # (that is the point of the detached-snapshot design), so an uncommitted
        # row is invisible to it and the catalogue comes back empty.
        await session.commit()
        return user.id, server.id


def test_a_transient_tool_call_failure_is_retried_and_can_succeed() -> None:
    """The cold-start case: first connect fails, second works.

    Without this the user reads a working server as broken, retries by hand, and it
    works — the behaviour that makes an integration feel unreliable.
    """
    ensure_schema()

    async def scenario() -> None:
        user_id, _ = await _seed_server()
        attempts = {"n": 0}

        class _FakeSession:
            async def call_tool(self, name, args):  # noqa: ANN001, ARG002
                class _R:
                    content = [type("B", (), {"type": "text", "text": "did it"})()]
                    structured_content = None
                    is_error = False
                return _R()

        def fake_open_session(server, attempt=None):  # noqa: ANN001, ARG001
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def cm():
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise httpx2.ConnectTimeout("cold start")
                yield _FakeSession()

            return cm()

        original = mcp_client.open_session
        mcp_client.open_session = fake_open_session
        # Keep the test fast without asserting on the constant's value.
        original_backoff = mcp_registry.CALL_BACKOFF_S
        mcp_registry.CALL_BACKOFF_S = 0.0
        try:
            out = await mcp_registry.call_tool(user_id, "mcp__Flaky__do_thing", {})
        finally:
            mcp_client.open_session = original
            mcp_registry.CALL_BACKOFF_S = original_backoff

        assert attempts["n"] == 2, f"expected one retry, got {attempts['n']} attempts"
        assert "did it" in out

    run_async(scenario())


def test_a_permanent_failure_is_attempted_once() -> None:
    """A 401 must not be retried: it is the answer, and the user needs the
    Authorize button rather than a second identical round trip."""
    ensure_schema()

    async def scenario() -> None:
        user_id, _ = await _seed_server(name="Locked")
        attempts = {"n": 0}

        def fake_open_session(server, attempt=None):  # noqa: ANN001, ARG001
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def cm():
                attempts["n"] += 1
                raise mcp_client.McpAuthRequired("authorize first")
                yield  # pragma: no cover

            return cm()

        original = mcp_client.open_session
        mcp_client.open_session = fake_open_session
        try:
            out = await mcp_registry.call_tool(user_id, "mcp__Locked__do_thing", {})
        finally:
            mcp_client.open_session = original

        assert attempts["n"] == 1, f"a 401 was retried {attempts['n']} times"
        assert "authoriz" in out.lower()

    run_async(scenario())


def test_retries_are_bounded() -> None:
    """A server that is genuinely down must not be hammered, and the user must not
    wait through an unbounded ladder while the turn is open."""
    ensure_schema()

    async def scenario() -> None:
        user_id, _ = await _seed_server(name="Down")
        attempts = {"n": 0}

        def fake_open_session(server, attempt=None):  # noqa: ANN001, ARG001
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def cm():
                attempts["n"] += 1
                raise httpx2.ConnectTimeout("still down")
                yield  # pragma: no cover

            return cm()

        original = mcp_client.open_session
        mcp_client.open_session = fake_open_session
        original_backoff = mcp_registry.CALL_BACKOFF_S
        mcp_registry.CALL_BACKOFF_S = 0.0
        try:
            out = await mcp_registry.call_tool(user_id, "mcp__Down__do_thing", {})
        finally:
            mcp_client.open_session = original
            mcp_registry.CALL_BACKOFF_S = original_backoff

        assert attempts["n"] == mcp_registry.CALL_ATTEMPTS
        # Still a tool RESULT, not an exception: the model made a call and must get
        # a result for it or the next request is malformed.
        assert "failed" in out.lower()

    run_async(scenario())


def test_a_cancelled_call_is_not_retried() -> None:
    """The caller is gone; retrying would keep work alive after the request that
    wanted it was abandoned."""
    ensure_schema()

    async def scenario() -> None:
        user_id, _ = await _seed_server(name="Cancelled")
        attempts = {"n": 0}

        def fake_open_session(server, attempt=None):  # noqa: ANN001, ARG001
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def cm():
                attempts["n"] += 1
                raise asyncio.CancelledError()
                yield  # pragma: no cover

            return cm()

        original = mcp_client.open_session
        mcp_client.open_session = fake_open_session
        try:
            raised = False
            try:
                await mcp_registry.call_tool(user_id, "mcp__Cancelled__do_thing", {})
            except asyncio.CancelledError:
                raised = True
        finally:
            mcp_client.open_session = original

        assert raised, "CancelledError must propagate, not become a tool result"
        assert attempts["n"] == 1

    run_async(scenario())


def test_a_stale_catalogue_refreshes_itself() -> None:
    """The server renamed a tool. Retrying the old name cannot work, so the cache
    is re-listed and the model is told what the tools ARE — which makes the next
    turn succeed instead of failing identically for ever."""
    ensure_schema()

    async def scenario() -> None:
        user_id, server_id = await _seed_server(name="Renamer")

        def fake_open_session(server, attempt=None):  # noqa: ANN001, ARG001
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def cm():
                raise RuntimeError("Unknown tool: do_thing")
                yield  # pragma: no cover

            return cm()

        async def fake_refresh(server):  # noqa: ANN001
            fresh = [
                {"name": "do_thing_v2", "description": "renamed", "input_schema": {}}
            ]
            async with session_scope() as session:
                row = await session.get(McpServer, server.id)
                row.tools_json = fresh
                await session.commit()
            return fresh

        orig_open = mcp_client.open_session
        orig_refresh = mcp_registry.refresh_server_tools
        mcp_client.open_session = fake_open_session
        mcp_registry.refresh_server_tools = fake_refresh
        try:
            out = await mcp_registry.call_tool(user_id, "mcp__Renamer__do_thing", {})
        finally:
            mcp_client.open_session = orig_open
            mcp_registry.refresh_server_tools = orig_refresh

        assert "no longer has a tool" in out
        assert "do_thing_v2" in out, "the model should be told what the tools now are"

        # And the cache was corrected, so the phantom tool is not offered again.
        after = await mcp_registry.load_catalogue(user_id)
        assert [t.tool_name for t in after.tools] == ["do_thing_v2"]

    run_async(scenario())


# ── one unusable tool must not cost the others ───────────────────────────────


def test_an_unusable_cached_entry_is_quarantined() -> None:
    """A third-party catalogue can contain anything. One bad entry drops itself."""
    ensure_schema()

    async def scenario() -> None:
        async with session_scope() as session:
            user = User(email=f"mixed-{os.urandom(4).hex()}@example.com",
                        password_hash="x")
            session.add(user)
            await session.flush()
            session.add(
                McpServer(
                    user_id=user.id,
                    name="Mixed",
                    config={"url": "https://example.com/mcp"},
                    auth_mode="none",
                    enabled=True,
                    tools_json=[
                        {"name": "good", "description": "fine", "input_schema": {}},
                        {"name": "", "description": "no name"},          # dropped
                        {"description": "no name key"},                   # dropped
                        "not even a dict",                                # dropped
                        {"name": "odd", "input_schema": {"anyOf": [
                            {"type": "object", "properties": {"a": {"type": "string"}}}
                        ]}},
                        {"name": "also_good", "description": 12345,
                         "input_schema": {"type": "string"}},
                    ],
                )
            )
            await session.commit()
            user_id = user.id

        catalogue = await mcp_registry.load_catalogue(user_id)
        names = sorted(t.tool_name for t in catalogue.tools)
        assert names == ["also_good", "good", "odd"], names

        # And every surviving tool renders to a shape the API accepts.
        for entry in catalogue.openai_tools():
            params = entry["function"]["parameters"]
            assert params["type"] == "object"
            for forbidden in ("anyOf", "oneOf", "allOf", "enum", "const", "not"):
                assert forbidden not in params

    run_async(scenario())


def test_a_malformed_schema_survives_the_round_trip_through_the_cache() -> None:
    """The higgsfield case, end to end: a top-level `anyOf` in `tools_json`
    reaches the model as a valid object schema rather than a 400."""
    ensure_schema()

    async def scenario() -> None:
        async with session_scope() as session:
            user = User(email=f"anyof-{os.urandom(4).hex()}@example.com",
                        password_hash="x")
            session.add(user)
            await session.flush()
            session.add(
                McpServer(
                    user_id=user.id,
                    name="Higgs",
                    config={"url": "https://example.com/mcp"},
                    auth_mode="none",
                    enabled=True,
                    tools_json=[
                        {
                            "name": "video_analysis_create",
                            "description": "analyse a video",
                            "input_schema": {
                                "anyOf": [
                                    {
                                        "type": "object",
                                        "properties": {
                                            "video_url": {"type": "string"},
                                            "prompt": {"type": "string"},
                                        },
                                        "required": ["video_url", "prompt"],
                                    },
                                    {
                                        "type": "object",
                                        "properties": {
                                            "video_id": {"type": "string"},
                                            "prompt": {"type": "string"},
                                        },
                                        "required": ["video_id", "prompt"],
                                    },
                                ]
                            },
                        }
                    ],
                )
            )
            await session.commit()
            user_id = user.id

        catalogue = await mcp_registry.load_catalogue(user_id)
        assert len(catalogue.tools) == 1

        params = catalogue.openai_tools()[0]["function"]["parameters"]
        assert params["type"] == "object"
        assert "anyOf" not in params
        # Both call shapes stay reachable, and only the universally-required key
        # is marked required.
        assert set(params["properties"]) == {"video_url", "video_id", "prompt"}
        assert params.get("required") == ["prompt"]

        # Voice renders the same shape, where a rejection would kill the CALL.
        voice = catalogue.deepgram_functions()[0]["parameters"]
        assert voice["type"] == "object" and "anyOf" not in voice

    run_async(scenario())


def test_without_tool_leaves_the_rest_of_the_catalogue_intact() -> None:
    """The chat heal ladder's primitive: drop one function, keep the others —
    including the other tools on the same server."""
    ensure_schema()

    async def scenario() -> None:
        async with session_scope() as session:
            user = User(email=f"drop-{os.urandom(4).hex()}@example.com",
                        password_hash="x")
            session.add(user)
            await session.flush()
            session.add(
                McpServer(
                    user_id=user.id,
                    name="Multi",
                    config={"url": "https://example.com/mcp"},
                    auth_mode="none",
                    enabled=True,
                    tools_json=[
                        {"name": "a", "input_schema": {}},
                        {"name": "b", "input_schema": {}},
                        {"name": "c", "input_schema": {}},
                    ],
                )
            )
            await session.commit()
            user_id = user.id

        catalogue = await mcp_registry.load_catalogue(user_id)
        assert len(catalogue.tools) == 3

        reduced = catalogue.without_tool("mcp__Multi__b")
        assert sorted(t.tool_name for t in reduced.tools) == ["a", "c"]
        # The original is untouched — it is a value, not a mutable buffer.
        assert len(catalogue.tools) == 3
        # And the prompt note still names the server, because tools remain.
        assert "Multi" in mcp_registry.tool_prompt_note(reduced)

        # Dropping every tool empties the note rather than naming a server with
        # nothing behind it.
        empty = reduced.without_tool("mcp__Multi__a").without_tool("mcp__Multi__c")
        assert empty.tools == ()
        assert mcp_registry.tool_prompt_note(empty) == ""

    run_async(scenario())


if __name__ == "__main__":
    raise SystemExit(conftest.run_standalone(dict(globals())))
