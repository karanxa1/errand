"""End-to-end against a REAL MCP server, and the OAuth rendezvous.

Everything else in the MCP suite tests our own logic in isolation. This module
stands up an actual MCP server (the SDK's own server half, over streamable HTTP on
loopback) and drives it through the real client path — connect, initialize,
tools/list, tools/call — because the parts most likely to break on an SDK upgrade
are exactly the parts a mock cannot cover: field naming (2.0.0 is snake_case where
the wire format is camelCase), transport construction, and result shapes.

It also pins the OAuth rendezvous, which is the one place this feature could not
copy its reference implementation. The MCP Python SDK holds `state` and the PKCE
verifier in a local stack frame, so the connecting coroutine has to survive the
browser round-trip; the test parks a flow, resolves it the way the callback route
does, and asserts the exchange completed with the right code and verifier.

Loopback means MCP_ALLOW_INSECURE_HTTP is required, which is exactly what that
setting is for.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conftest  # noqa: E402
from conftest import ensure_schema, run_async, session_scope  # noqa: E402

from app.config import settings  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ServerThread:
    """A real MCP server on loopback, for the duration of a with-block."""

    def __init__(self, app, port: int) -> None:
        self._app = app
        self.port = port
        self._server = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_ServerThread":
        import uvicorn

        config = uvicorn.Config(
            self._app, host="127.0.0.1", port=self.port, log_level="error"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        # Wait for the port to accept, rather than sleeping a guessed interval.
        for _ in range(150):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return self
            except OSError:
                threading.Event().wait(0.05)
        raise RuntimeError("test MCP server did not start")

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)


def _mcp_app():
    """An MCP server with two tools, one of which fails on purpose."""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("errand-test-server")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @server.tool()
    def boom() -> str:
        """Always fails, to exercise the error path."""
        raise RuntimeError("intentional failure")

    @server.tool()
    async def slow() -> str:
        """Takes long enough to observe what is held while a call is in flight."""
        await asyncio.sleep(SLOW_TOOL_S)
        return "done"

    return server.streamable_http_app()


# Long enough that the pool watcher gets many samples inside the call window, short
# enough not to slow the suite. A real tool call is far slower than this.
SLOW_TOOL_S = 0.6


async def _register(session, user_id: str, url: str, name: str):
    from app.models import McpServer

    server = McpServer(
        user_id=user_id, name=name, config={"url": url, "transport": "http"},
        auth_mode="none", enabled=True, last_status="unknown",
    )
    session.add(server)
    await session.commit()
    await session.refresh(server)
    return server


def test_a_real_server_round_trips_from_registration_to_tool_call() -> None:
    """The whole path, against a genuine MCP server.

    Covers what no mock can: transport construction, `initialize`, the snake_case
    field names 2.0.0 uses, catalogue caching, namespaced ids, and both the success
    and failure shapes of a tool result.
    """
    ensure_schema()
    port = _free_port()
    original = settings.mcp_allow_insecure_http
    settings.mcp_allow_insecure_http = True  # loopback; see the module docstring
    try:
        with _ServerThread(_mcp_app(), port):
            url = f"http://127.0.0.1:{port}/mcp"

            async def scenario() -> None:
                from app.mcp import registry
                from app.mcp.client import open_session
                from conftest import create_user

                async with session_scope() as session:
                    user = await create_user(session)
                    server = await _register(session, user.id, url, "Calc")
                    user_id, server_id = user.id, server.id

                # 1. A live connection lists the real tools.
                async with session_scope() as session:
                    row = await session.get(type(server), server_id)
                    async with open_session(row) as client:
                        listed = await client.list_tools()
                names = sorted(t.name for t in listed.tools)
                assert names == ["add", "boom", "slow"], names

                # 2. Refreshing caches the catalogue on the row.
                async with session_scope() as session:
                    row = await session.get(type(server), server_id)
                    catalogue = await registry.refresh_server_tools(row)
                assert sorted(entry["name"] for entry in catalogue) == [
                    "add",
                    "boom",
                    "slow",
                ]
                # The schema survives, which is what the model needs to call it.
                add_entry = next(e for e in catalogue if e["name"] == "add")
                assert add_entry["input_schema"]["properties"].keys() >= {"a", "b"}

                async with session_scope() as session:
                    row = await session.get(type(server), server_id)
                    assert row.last_status == "connected"
                    assert row.last_error is None
                    assert row.tools_updated_at is not None

                # 3. The catalogue the model sees, built with no network I/O.
                loaded = await registry.load_catalogue(user_id)
                ids = sorted(t.tool_id for t in loaded.tools)
                assert ids == [
                    "mcp__Calc__add",
                    "mcp__Calc__boom",
                    "mcp__Calc__slow",
                ], ids

                # 4. A real tool call through the dispatch the chat path uses.
                result = await registry.call_tool(
                    user_id, "mcp__Calc__add", {"a": 2, "b": 40}
                )
                assert "42" in result, result

                # 5. A failing tool comes back as TEXT, never as an exception —
                # the model made a call and must get a result for it.
                failed = await registry.call_tool(user_id, "mcp__Calc__boom", {})
                assert isinstance(failed, str)
                assert "error" in failed.lower() or "fail" in failed.lower()

                # 6. And an unknown id is refused rather than attempted.
                unknown = await registry.call_tool(user_id, "mcp__Calc__nope", {})
                assert "unknown tool" in unknown.lower()

            run_async(scenario())
    finally:
        settings.mcp_allow_insecure_http = original


def test_bad_arguments_come_back_as_a_readable_tool_result() -> None:
    """A schema violation is the server's answer, not our exception."""
    ensure_schema()
    port = _free_port()
    original = settings.mcp_allow_insecure_http
    settings.mcp_allow_insecure_http = True
    try:
        with _ServerThread(_mcp_app(), port):
            url = f"http://127.0.0.1:{port}/mcp"

            async def scenario() -> None:
                from app.mcp import registry
                from app.models import McpServer
                from conftest import create_user

                async with session_scope() as session:
                    user = await create_user(session)
                    server = await _register(session, user.id, url, "Calc2")
                    user_id, server_id = user.id, server.id

                async with session_scope() as session:
                    row = await session.get(McpServer, server_id)
                    await registry.refresh_server_tools(row)

                result = await registry.call_tool(
                    user_id, "mcp__Calc2__add", {"a": "not-a-number"}
                )
                assert isinstance(result, str) and result
                # The server's validation message is surfaced, so the model can
                # correct itself rather than just seeing "it failed".
                assert "a" in result

            run_async(scenario())
    finally:
        settings.mcp_allow_insecure_http = original


def test_an_unreachable_server_records_an_error_without_raising() -> None:
    """A dead server must degrade, not take the turn down."""
    ensure_schema()
    port = _free_port()  # nothing listening
    original = settings.mcp_allow_insecure_http
    settings.mcp_allow_insecure_http = True
    try:

        async def scenario() -> None:
            from app.mcp import registry
            from app.models import McpServer
            from conftest import create_user

            async with session_scope() as session:
                user = await create_user(session)
                server = await _register(
                    session, user.id, f"http://127.0.0.1:{port}/mcp", "Dead"
                )
                user_id, server_id = user.id, server.id
                server.tools_json = [
                    {"name": "ping", "description": "", "input_schema": {}}
                ]
                await session.commit()

            result = await registry.call_tool(user_id, "mcp__Dead__ping", {})
            assert isinstance(result, str)
            assert "failed" in result.lower() or "reach" in result.lower()

            async with session_scope() as session:
                row = await session.get(McpServer, server_id)
                assert row.last_status == "error"
                assert row.last_error
                # The message has to be readable, not an anyio ExceptionGroup
                # summary — that is what describe_error exists for.
                assert "taskgroup" not in row.last_error.lower()

            run_async_done.append(True)

        run_async_done: list[bool] = []
        run_async(scenario())
        assert run_async_done == [True]
    finally:
        settings.mcp_allow_insecure_http = original


def test_a_tool_call_holds_no_database_connection_while_it_runs() -> None:
    """The pool must not be leased for the duration of a remote call.

    A tool call can run for minutes, and an OAuth authorization parks for up to
    five waiting for a human. Holding a pooled connection across either means a
    handful of concurrent users starve the pool and stall every unrelated request —
    including the SSE streams and the approval-gate polling, which open their own
    short-lived sessions constantly.

    Pinned by watching the engine's checked-out count DURING the call, from inside
    the transport, rather than by inspecting the code: the failure mode is a
    session-scope regression, and only a live measurement catches it.
    """
    ensure_schema()
    port = _free_port()
    original = settings.mcp_allow_insecure_http
    settings.mcp_allow_insecure_http = True
    try:
        with _ServerThread(_mcp_app(), port):
            url = f"http://127.0.0.1:{port}/mcp"

            async def scenario() -> None:
                from app.db import engine
                from app.mcp import registry
                from app.models import McpServer
                from conftest import create_user

                async with session_scope() as session:
                    user = await create_user(session)
                    server = await _register(session, user.id, url, "PoolCheck")
                    user_id, server_id = user.id, server.id

                async with session_scope() as session:
                    row = await session.get(McpServer, server_id)
                    await registry.refresh_server_tools(row)

                pool = engine.pool
                observed: list[int] = []
                in_flight = True

                # Sample ONLY while the call is in flight. Sampling past the end
                # would collect the zeros that follow it and pass regardless, which
                # is exactly how the first version of this test was vacuous — it was
                # verified to still pass with the bug deliberately reintroduced.
                async def watch() -> None:
                    while in_flight:
                        observed.append(pool.checkedout())
                        await asyncio.sleep(0.02)

                watcher = asyncio.create_task(watch())
                result = await registry.call_tool(user_id, "mcp__PoolCheck__slow", {})
                in_flight = False
                await watcher

                assert "done" in result, result
                # Enough samples that the window was genuinely observed.
                assert len(observed) >= 5, f"only {len(observed)} samples taken"
                # A connection held for the call shows as a non-zero floor across
                # every in-flight sample.
                assert min(observed) == 0, (
                    f"a database connection was held for the whole tool call "
                    f"(in-flight checked-out samples: min={min(observed)}, "
                    f"max={max(observed)}, n={len(observed)})"
                )

            run_async(scenario())
    finally:
        settings.mcp_allow_insecure_http = original


def test_the_oauth_rendezvous_parks_and_resumes() -> None:
    """The core of the OAuth design, pinned end to end.

    `redirect_handler` publishes the URL and the flow PARKS in `callback_handler`
    until the callback route resolves it — because the SDK keeps `state` and the
    PKCE verifier in that stack frame and offers no way to rebuild them elsewhere.

    Asserts the three things that would silently break it: the URL is published and
    carries a state, resolving by that state wakes the right flow, and the code and
    PKCE verifier reach the token endpoint.
    """
    ensure_schema()
    from urllib.parse import parse_qs, urlparse

    import httpx2
    from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
    from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
    from pydantic import AnyUrl

    from app.mcp import pending

    token_requests: list[dict] = []

    class _Memory:
        def __init__(self) -> None:
            self.tokens: OAuthToken | None = None
            self.info: OAuthClientInformationFull | None = None

        async def get_tokens(self):
            return self.tokens

        async def set_tokens(self, tokens):
            self.tokens = tokens

        async def get_client_info(self):
            return self.info

        async def set_client_info(self, info):
            self.info = info

    def _idp_app():
        """A minimal authorization server: discovery, registration, token."""
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route

        issuer = {"value": ""}

        async def protected(_request):
            return Response(
                status_code=401,
                headers={
                    "WWW-Authenticate": (
                        f'Bearer resource_metadata='
                        f'"{issuer["value"]}/.well-known/oauth-protected-resource"'
                    )
                },
            )

        async def prm(_request):
            return JSONResponse(
                {
                    "resource": f"{issuer['value']}/mcp",
                    "authorization_servers": [issuer["value"]],
                }
            )

        async def asm(_request):
            base = issuer["value"]
            return JSONResponse(
                {
                    "issuer": base,
                    "authorization_endpoint": f"{base}/authorize",
                    "token_endpoint": f"{base}/token",
                    "registration_endpoint": f"{base}/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"],
                }
            )

        async def register(request):
            body = await request.json()
            return JSONResponse(
                {
                    "client_id": "test-client",
                    "client_name": body.get("client_name"),
                    "redirect_uris": body.get("redirect_uris"),
                    "grant_types": body.get(
                        "grant_types", ["authorization_code", "refresh_token"]
                    ),
                    "response_types": body.get("response_types", ["code"]),
                    "token_endpoint_auth_method": body.get(
                        "token_endpoint_auth_method", "none"
                    ),
                },
                status_code=201,
            )

        async def token(request):
            token_requests.append(dict(await request.form()))
            return JSONResponse(
                {
                    "access_token": "issued-access-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )

        app = Starlette(
            routes=[
                Route("/mcp", protected, methods=["GET", "POST"]),
                Route("/.well-known/oauth-protected-resource", prm),
                Route("/.well-known/oauth-authorization-server", asm),
                Route("/.well-known/openid-configuration", asm),
                Route("/register", register, methods=["POST"]),
                Route("/token", token, methods=["POST"]),
            ]
        )
        return app, issuer

    port = _free_port()
    app, issuer = _idp_app()
    issuer["value"] = f"http://127.0.0.1:{port}"

    pending.clear()
    try:
        with _ServerThread(app, port):

            async def scenario() -> None:
                attempt = pending.start("user-1", "server-1")
                storage = _Memory()

                async def on_redirect(url: str) -> None:
                    pending.publish_url(attempt, url)

                async def on_callback() -> AuthorizationCodeResult:
                    code, state, iss = await pending.wait_for_code(attempt)
                    return AuthorizationCodeResult(code=code, state=state, iss=iss)

                provider = OAuthClientProvider(
                    server_url=f"{issuer['value']}/mcp",
                    client_metadata=OAuthClientMetadata(
                        client_name="Errand",
                        redirect_uris=[
                            AnyUrl("http://localhost:8787/api/mcp/oauth/callback")
                        ],
                        scope="mcp:tools",
                    ),
                    storage=storage,
                    redirect_handler=on_redirect,
                    callback_handler=on_callback,
                )

                async def drive() -> None:
                    """The parked flow: one request that provokes the whole dance."""
                    async with httpx2.AsyncClient(
                        auth=provider, follow_redirects=True
                    ) as http:
                        await http.post(f"{issuer['value']}/mcp", json={})

                task = asyncio.create_task(drive())

                # The URL is published — this is what the POST /authorize returns.
                url = await pending.wait_for_url(attempt, timeout=30.0)
                assert url.startswith(f"{issuer['value']}/authorize")
                query = parse_qs(urlparse(url).query)
                assert query["code_challenge_method"] == ["S256"]
                sdk_state = query["state"][0]

                # The flow is parked, and reachable by the SDK's own state — which
                # is the index the callback route uses, because the redirect URI is
                # fixed and cannot carry our attempt id.
                assert attempt.oauth_state == sdk_state
                assert not attempt.code_ready.is_set()

                # Now the browser comes back. Exactly what the callback route does.
                pending.resolve(sdk_state, code="granted-code", iss=None)

                await asyncio.wait_for(task, timeout=30.0)

                # The exchange happened, with the code and a PKCE verifier.
                assert len(token_requests) == 1, token_requests
                exchange = token_requests[0]
                assert exchange["grant_type"] == "authorization_code"
                assert exchange["code"] == "granted-code"
                assert exchange["code_verifier"]
                assert (
                    exchange["redirect_uri"]
                    == "http://localhost:8787/api/mcp/oauth/callback"
                )
                # And the token was handed to storage, i.e. it would persist.
                assert storage.tokens is not None
                assert storage.tokens.access_token == "issued-access-token"

            run_async(scenario())
    finally:
        pending.clear()


def test_a_spent_authorization_code_is_not_replayed() -> None:
    """Single-use delivery, and why it matters.

    The SDK can re-enter the authorization grant (a server that answers 401 even
    with a valid token drives it round twice). Handing back the same code would
    make the token endpoint reject it as `invalid_grant`, in a loop. Failing the
    second ask turns that into one legible error.
    """
    ensure_schema()
    from app.mcp import pending

    pending.clear()
    try:

        async def scenario() -> None:
            attempt = pending.start("user-2", "server-2")
            pending.publish_url(attempt, "https://idp.example/authorize?state=abc123")
            pending.resolve("abc123", code="one-time-code", iss=None)

            code, state, _ = await pending.wait_for_code(attempt)
            assert code == "one-time-code"
            assert state == "abc123"

            try:
                await pending.wait_for_code(attempt)
            except pending.McpAuthError as exc:
                assert "already used" in str(exc).lower()
            else:
                raise AssertionError("a spent code must not be delivered twice")

        run_async(scenario())
    finally:
        pending.clear()


def test_a_stale_or_forged_callback_state_is_inert() -> None:
    """The callback carries no authority of its own."""
    ensure_schema()
    from app.mcp import pending

    pending.clear()
    try:
        for state in ("never-issued", "", "../../etc/passwd"):
            try:
                pending.resolve(state, code="c", iss=None)
            except pending.McpAuthError:
                pass
            else:
                raise AssertionError(f"state {state!r} should not resolve anything")
    finally:
        pending.clear()


def test_a_new_attempt_supersedes_the_previous_one_for_the_same_server() -> None:
    """Clicking Authorize twice must not leak the first parked coroutine."""
    ensure_schema()
    from app.mcp import pending

    pending.clear()
    try:

        async def scenario() -> None:
            first = pending.start("user-3", "server-3")
            pending.publish_url(first, "https://idp.example/authorize?state=first")
            second = pending.start("user-3", "server-3")
            assert second.id != first.id

            # The first is woken with an error so its flow unwinds.
            assert first.code_ready.is_set()
            try:
                await pending.wait_for_code(first)
            except pending.McpAuthError as exc:
                assert "superseded" in str(exc).lower()
            else:
                raise AssertionError("the superseded attempt should fail")

            # And its state no longer routes anywhere.
            try:
                pending.resolve("first", code="c", iss=None)
            except pending.McpAuthError:
                pass
            else:
                raise AssertionError("a superseded state must be unroutable")

        run_async(scenario())
    finally:
        pending.clear()


def test_concurrent_attempts_per_user_are_capped() -> None:
    """Each parked attempt is a coroutine and a socket; unbounded is a leak."""
    ensure_schema()
    from app.mcp import pending

    pending.clear()
    try:
        for index in range(pending.MAX_PENDING_PER_USER):
            pending.start("user-4", f"server-{index}")
        try:
            pending.start("user-4", "server-overflow")
        except pending.McpAuthError as exc:
            assert "too many" in str(exc).lower()
        else:
            raise AssertionError("the per-user cap should be enforced")
        # A different user is unaffected.
        assert pending.start("user-5", "server-x") is not None
    finally:
        pending.clear()


if __name__ == "__main__":
    raise SystemExit(conftest.run_standalone(dict(globals())))
