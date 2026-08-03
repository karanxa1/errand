"""Connect to one MCP server, list its tools, call a tool.

Structure follows better-chatbot's MCPClient (src/lib/ai/mcp/create-mcp-client.ts):
try streamable HTTP, fall back to SSE, discover the need for auth LAZILY by
catching a 401 and retrying with the auth provider attached, and never let a tool
failure escape as an exception — it is reported back to the model as the tool
result, because a model that made a call must get a result for it.

CONNECT PER OPERATION, NOT A PERSISTENT POOL. better-chatbot holds every client
open with a 30-minute idle disconnect. That is a good fit for a Node process
holding one manager; it is a poor fit here, and the reason is the SDK's shape:
`Client` is an async context manager wrapping an anyio task group, so keeping one
open means owning a long-lived task per server per user and tearing it down
correctly across restarts, SSE stream teardowns and cancellations. The cost of
not doing that — one connect + initialize per tool call — is only paid on an
actual invocation, because the tool LIST that every turn needs is served from the
`tools_json` cache on the server row (which is better-chatbot's `toolInfo` idea,
and the part that actually matters for latency). A tool call already involves a
remote round trip, so one extra handshake is noise next to it.

TRANSPORT NOTES, doc-verified against the installed SDK (mcp 2.0.0) rather than
extrapolated, per this repo's provider rule:
  * `streamable_http_client(url, *, http_client=None, terminate_on_close=True)`
    takes NO `headers=` or `timeout=` — those moved onto the httpx2 client, and
    passing them raises TypeError. Auth attaches as `httpx2.AsyncClient(auth=...)`.
  * `sse_client(url, headers=None, timeout=5.0, sse_read_timeout=300.0, ...,
    auth=None)` still takes both directly.
  * Tool fields are snake_case in 2.0.0 (`input_schema`, `is_error`,
    `structured_content`), not the camelCase of the 1.x line and of the wire
    format.
  https://py.sdk.modelcontextprotocol.io/v2/client/transports/
  https://py.sdk.modelcontextprotocol.io/v2/client/oauth-clients/

⚠️ `httpx2` BELOW IS NOT A TYPO, AND NOT `httpx`.
mcp 2.0.0 depends on `httpx2`, which is a SEPARATE DISTRIBUTION from the `httpx`
the brokers use — not a major version of it. Both are installed and both are
imported in this process, deliberately: this module is the only place that uses
`httpx2`, and every broker keeps using `httpx`. They share no module namespace and
no connection pool, so they do not interact.

The trap that follows from that: `httpx2.HTTPError` does NOT inherit from
`httpx.HTTPError`. The two hierarchies are disjoint, so a broad
`except httpx.HTTPError` would silently fail to catch an MCP transport failure.
The handlers below name the `httpx2` types on purpose. See
docs/decisions/mcp-sdk-dependency.md for why the SDK is a dependency at all
rather than the protocol being hand-rolled.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx2  # NOT httpx — see the module docstring.
from mcp import Client, StdioServerParameters
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata
from pydantic import AnyUrl

from app.config import settings
from app.mcp import pending, storage
from app.mcp.config import (
    McpConfigError,
    is_stdio,
    transport_of,
    validate_remote_url,
)
from app.mcp.crypto import decrypt_json

logger = logging.getLogger("errand.mcp.client")

# Connect budget. A cold MCP server behind a cold Lambda can take a few seconds
# to answer `initialize`; a minute is past the point where a user is still
# waiting. Read timeout is long because a tool call is the slow part and some
# tools legitimately run for minutes.
CONNECT_TIMEOUT_S = 30.0
READ_TIMEOUT_S = 300.0

# What we register ourselves as, when a server supports dynamic client
# registration (RFC 7591). `token_endpoint_auth_method` is left to the SDK's
# default so a public PKCE client is negotiated correctly.
OAUTH_CLIENT_NAME = "Errand"
OAUTH_SCOPE = "mcp:tools"


class McpConnectionError(RuntimeError):
    """A connection that failed for a reason worth showing the user."""


class McpAuthRequired(RuntimeError):
    """The server needs OAuth and we have no token for it yet.

    Distinct from a connection error because the remedy is different and the UI
    renders it differently: this is "click Authorize", not "check the URL".
    """


def _unwrap(exc: BaseException) -> BaseException:
    """The most specific cause inside an anyio ExceptionGroup.

    The SDK runs its transport in an anyio task group, so essentially every
    failure arrives wrapped — often twice. Reporting the group verbatim gives the
    user "unhandled errors in a TaskGroup (1 sub-exception)", which says nothing.
    This digs out the leaf so the message names the actual problem.
    """
    seen: set[int] = set()
    current: BaseException = exc
    while id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, BaseExceptionGroup) and current.exceptions:
            current = current.exceptions[0]
            continue
        if current.__cause__ is not None:
            current = current.__cause__
            continue
        break
    return current


def describe_error(exc: BaseException) -> str:
    """A one-line, user-facing reason a connection failed."""
    leaf = _unwrap(exc)
    text = str(leaf).strip()
    if isinstance(leaf, httpx2.ConnectError):
        return "Could not reach the server. Check the URL."
    if isinstance(leaf, httpx2.ConnectTimeout | httpx2.ReadTimeout):
        return "The server did not respond in time."
    if isinstance(leaf, httpx2.HTTPStatusError):
        return f"The server returned HTTP {leaf.response.status_code}."
    if not text:
        return type(leaf).__name__
    return text if len(text) <= 300 else text[:297] + "…"


def _is_unauthorized(exc: BaseException) -> bool:
    """Whether this failure means "you need to authenticate".

    Mirrors better-chatbot's `isUnauthorized`: the SDK does not always surface a
    typed error for this, so a 401 has to be recognized from a status code, from
    an httpx status error, or from the message text. Broad on purpose — a missed
    401 shows up as an unexplained connection failure and the user never gets
    offered the Authorize button.
    """
    leaf = _unwrap(exc)
    if isinstance(leaf, httpx2.HTTPStatusError):
        return leaf.response.status_code in (401, 403)
    status = getattr(leaf, "status", None) or getattr(leaf, "status_code", None)
    if status in (401, 403):
        return True
    text = str(leaf).lower()
    return any(
        marker in text
        for marker in (
            "401",
            "unauthorized",
            "invalid_token",
            "authentication required",
            "www-authenticate",
        )
    )


def is_transient(exc: BaseException) -> bool:
    """Whether retrying this exact request could plausibly succeed.

    THE CASE THIS EXISTS FOR is a cold start. A large share of real MCP servers run
    on platforms that suspend when idle — Cloudflare Workers, Vercel functions, Fly
    machines, Cloud Run — so the FIRST connect after a quiet period can time out or
    be reset while the instance boots, and the second succeeds immediately. Without
    a retry the user reads that as "this server does not work", tries again by hand,
    and it works: the behaviour that makes an integration feel unreliable even
    though nothing is broken.

    WHAT MUST NOT BE RETRIED, and why each one is an ANSWER rather than a hiccup:
      * 401/403 — the server is telling us to authenticate. Retrying collects a
        second 401 and delays the Authorize button the user actually needs.
      * McpAuthRequired — same, already classified.
      * McpConfigError / an SSRF refusal — a deliberate decision by our own guard.
        Retrying a blocked destination is pointless and, if a DNS record is
        flapping between a public and a private address, actively dangerous: it is
        a second roll of the dice on a check that already said no.
      * 4xx other than 408/429 — deterministic. A 404 on the MCP path will 404
        again.
      * CancelledError — the caller is gone.
    """
    if isinstance(exc, asyncio.CancelledError | McpAuthRequired | McpConfigError):
        return False
    if _is_unauthorized(exc):
        return False

    leaf = _unwrap(exc)
    if isinstance(leaf, asyncio.CancelledError | McpAuthRequired | McpConfigError):
        return False

    # Our own guard refusing a destination. The message is generated in
    # _GuardedTransport and names the refusal explicitly.
    if isinstance(leaf, McpConnectionError) and "Refused a request to" in str(leaf):
        return False

    if isinstance(
        leaf,
        httpx2.ConnectError
        | httpx2.ConnectTimeout
        | httpx2.ReadTimeout
        | httpx2.WriteTimeout
        | httpx2.PoolTimeout
        | httpx2.RemoteProtocolError
        | httpx2.ReadError
        | httpx2.WriteError,
    ):
        return True
    if isinstance(leaf, httpx2.HTTPStatusError):
        return leaf.response.status_code in (408, 429, 500, 502, 503, 504)

    status = getattr(leaf, "status", None) or getattr(leaf, "status_code", None)
    if isinstance(status, int):
        return status in (408, 429, 500, 502, 503, 504)

    # Fall back to the message. Broad on purpose for the same reason
    # `_is_unauthorized` is: a missed transient failure costs a working tool call,
    # and the retry is bounded and idempotent-ish (a connect + one tool call).
    text = str(leaf).lower()
    return any(
        marker in text
        for marker in (
            "timed out",
            "timeout",
            "connection reset",
            "connection refused",
            "connection closed",
            "server disconnected",
            "temporarily unavailable",
            "bad gateway",
            "service unavailable",
            "gateway timeout",
            "eof occurred",
            "handshake",
        )
    )


def is_tool_missing(exc: BaseException) -> bool:
    """Whether the server says the tool we named does not exist.

    Means our cached `tools_json` is STALE: the server renamed or removed a tool
    since we last listed it. Distinguishable from a transport failure because the
    call reached the server and it answered. Used by registry.call_tool to refresh
    the cache so the phantom tool stops being offered on the next turn, rather than
    failing identically forever.
    """
    leaf = _unwrap(exc)
    text = str(leaf).lower()
    if "tool" not in text:
        return False
    return any(
        marker in text
        for marker in (
            "unknown tool",
            "tool not found",
            "no such tool",
            "not found",
            "unknown_tool",
            "invalid tool",
            "does not exist",
            "method not found",
        )
    )


class _GuardedTransport(httpx2.AsyncBaseTransport):
    """Re-checks the SSRF guard on EVERY request, not just at registration.

    ⚠️ WHY A TRANSPORT AND NOT A ONE-TIME CHECK.
    `app.mcp.config.validate_remote_url` runs when a server is REGISTERED. That is
    not sufficient on its own, because the client follows redirects: a registered,
    genuinely public `https://evil.example/mcp` can answer `302 Location:
    http://169.254.169.254/metadata/instance` and the request lands on Azure IMDS
    having passed every registration-time check. The same hole applies to the OAuth
    flow, which follows `WWW-Authenticate` and discovery documents to hosts nobody
    validated — an authorization server pointed at a private address is equally bad.

    A transport wrapper is the airtight seam: it is the last thing before the
    socket, and every request goes through it — the initial POST, each redirect hop,
    metadata discovery, dynamic registration and the token exchange. A `request`
    event hook would also fire per-redirect, but a transport cannot be bypassed.

    The check is skipped entirely when `mcp_allow_insecure_http` is on, which is the
    local-development escape hatch and is what its own validation does too.
    """

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if not settings.mcp_allow_insecure_http:
            try:
                validate_remote_url(str(request.url))
            except McpConfigError as exc:
                # Raised, not returned as a response: this must not look like a
                # server error the retry logic might paper over.
                raise McpConnectionError(
                    f"Refused a request to {request.url.host!r}: {exc}"
                ) from exc
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def static_headers(server) -> dict[str, str]:
    """The decrypted static headers for an `auth_mode='headers'` server."""
    if server.auth_mode != "headers":
        return {}
    payload = decrypt_json(server.secret_headers)
    if not isinstance(payload, dict):
        return {}
    return {str(k): str(v) for k, v in payload.items()}


def _oauth_provider(server, attempt: pending.PendingAuth | None):
    """An OAuthClientProvider for `server`, or None when we cannot authorize.

    `attempt` is present only for an interactive authorization. Without it the
    provider still gets built — so a STORED token is used, and refreshed
    automatically — but its handlers refuse, because there is no browser to send
    anyone to. That distinction is what lets a background tool call use an
    existing authorization while a call on an unauthorized server fails as
    McpAuthRequired instead of parking forever.
    """
    url = server.config.get("url")
    if not url:
        return None

    redirect_uri = f"{settings.mcp_oauth_redirect_base}/api/mcp/oauth/callback"

    async def on_redirect(authorization_url: str) -> None:
        if attempt is None:
            raise McpAuthRequired(
                "This server requires authorization. Connect it from the MCP "
                "settings panel first."
            )
        pending.publish_url(attempt, authorization_url)

    async def on_callback() -> AuthorizationCodeResult:
        if attempt is None:
            raise McpAuthRequired("This server requires authorization.")
        code, state, iss = await pending.wait_for_code(attempt)
        # `state` and `iss` are passed through EXACTLY as received. The SDK
        # compares them against what it generated and discovered, and they are the
        # CSRF and server-mix-up defences — normalizing or defaulting either one
        # would quietly disable a check.
        return AuthorizationCodeResult(code=code, state=state, iss=iss)

    return OAuthClientProvider(
        server_url=url,
        client_metadata=OAuthClientMetadata(
            client_name=OAUTH_CLIENT_NAME,
            redirect_uris=[AnyUrl(redirect_uri)],
            scope=OAUTH_SCOPE,
        ),
        storage=storage.DbTokenStorage(server.id, url),
        redirect_handler=on_redirect,
        callback_handler=on_callback,
    )


def _guarded_client_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
) -> httpx2.AsyncClient:
    """`McpHttpClientFactory` that installs the SSRF guard.

    `sse_client` builds its own httpx client and only exposes this factory seam, so
    this is how the guard reaches the SSE path. Signature must match the SDK's
    protocol exactly (headers/timeout/auth, all optional).
    https://py.sdk.modelcontextprotocol.io/v2/client/transports/
    """
    return httpx2.AsyncClient(
        headers=headers,
        auth=auth,
        follow_redirects=True,
        timeout=timeout or httpx2.Timeout(CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S),
        transport=_GuardedTransport(httpx2.AsyncHTTPTransport()),
    )


def _guarded_client(
    *, headers: dict[str, str] | None, auth: Any | None
) -> httpx2.AsyncClient:
    """The httpx client for the streamable-HTTP path, SSRF-guarded."""
    return _guarded_client_factory(
        headers=headers or None,
        timeout=httpx2.Timeout(CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S),
        auth=auth,
    )


@asynccontextmanager
async def _remote_session(
    server, *, auth: Any | None, force_sse: bool = False
) -> AsyncIterator[Client]:
    """An open Client over streamable HTTP, or SSE.

    Both transports are attempted by `open_session`; this opens exactly the one it
    is asked for so the fallback logic stays in one place.
    """
    url = server.config["url"]
    headers = static_headers(server)
    kind = "sse" if force_sse else transport_of(server.config)

    if kind == "sse":
        # sse_client still takes headers/timeout/auth directly (see module notes),
        # and builds its own client — so the SSRF guard is injected through its
        # `httpx_client_factory` seam rather than by handing it a client.
        async with sse_client(
            url,
            headers=headers or None,
            timeout=CONNECT_TIMEOUT_S,
            sse_read_timeout=READ_TIMEOUT_S,
            auth=auth,
            httpx_client_factory=_guarded_client_factory,
        ) as transport:
            async with Client(transport) as client:
                yield client
        return

    async with _guarded_client(headers=headers, auth=auth) as http_client:
        transport = streamable_http_client(url, http_client=http_client)
        async with Client(transport) as client:
            yield client


@asynccontextmanager
async def _stdio_session(server) -> AsyncIterator[Client]:
    """An open Client over a spawned subprocess.

    Only reachable when MCP_ALLOW_STDIO is on; app/mcp/config.validate_stdio
    refuses the config otherwise. Note the SDK gives the child a minimal env
    allow-list (HOME, LOGNAME, PATH, SHELL, TERM, USER on POSIX) rather than this
    process's full environment, which is a meaningful part of why this is
    survivable at all — provider keys are not inherited unless someone names them
    in `env`.
    """
    config = server.config
    params = StdioServerParameters(
        command=config["command"],
        args=list(config.get("args") or []),
        env=dict(config.get("env") or {}) or None,
    )
    async with Client(stdio_client(params)) as client:
        yield client


@asynccontextmanager
async def open_session(
    server, *, attempt: pending.PendingAuth | None = None
) -> AsyncIterator[Client]:
    """An open, initialized Client for `server`, authenticating as needed.

    The auth ladder, in order:
      1. stdio — no auth concept at all.
      2. auth_mode='headers' — the static secrets ride on the transport; no OAuth
         provider is built, so a 401 here is a wrong credential, not a prompt.
      3. auth_mode='oauth' with a stored token — the provider is attached from the
         start, which lets it refresh an expired access token without a human.
      4. auth_mode='none' — try unauthenticated FIRST, and only on a 401 retry
         with the provider. This is better-chatbot's lazy discovery, and it is why
         a user can paste a URL without having to know in advance whether it needs
         OAuth: the 401 tells us, and the server row is upgraded to 'oauth'.
    """
    if is_stdio(server.config):
        async with _stdio_session(server) as client:
            yield client
        return

    wants_oauth = server.auth_mode == "oauth" or attempt is not None
    if wants_oauth:
        provider = _oauth_provider(server, attempt)
        if provider is None:
            raise McpConnectionError("This server has no URL to authorize against.")
        async with _with_sse_fallback(server, provider) as client:
            yield client
        return

    if server.auth_mode == "headers":
        async with _with_sse_fallback(server, None) as client:
            yield client
        return

    # auth_mode == 'none': attempt open access, then discover OAuth from a 401.
    #
    # Same `yielded` guard as _with_sse_fallback, for the same reason: without it a
    # caller-body error that happened to look like a 401 (an MCP server whose tool
    # result mentions "unauthorized", say) would trigger the OAuth retry and a
    # second yield.
    yielded = False
    try:
        async with _with_sse_fallback(server, None) as client:
            yielded = True
            yield client
        return
    except asyncio.CancelledError:
        raise
    except (McpAuthRequired, McpConnectionError):
        raise
    except BaseException as exc:  # noqa: BLE001 — includes anyio ExceptionGroup
        if yielded:
            raise
        if not _is_unauthorized(exc):
            raise McpConnectionError(describe_error(exc)) from exc

    # A 401 with no stored credential and no browser attached is exactly the
    # "needs authorizing" state — the UI turns this into an Authorize button.
    if not await storage.has_tokens(server.id):
        raise McpAuthRequired(
            "This server requires authorization. Connect it from the MCP "
            "settings panel."
        )
    provider = _oauth_provider(server, attempt)
    if provider is None:
        raise McpConnectionError("This server has no URL to authorize against.")
    async with _with_sse_fallback(server, provider) as client:
        yield client


@asynccontextmanager
async def _with_sse_fallback(server, auth: Any | None) -> AsyncIterator[Client]:
    """Streamable HTTP, falling back to SSE, as better-chatbot does.

    A 401 is re-raised rather than retried on the other transport: it is an
    ANSWER, and the caller's ladder is what decides what to do about it. Retrying
    SSE on a 401 would just collect a second 401 and bury the first.
    """
    kind = transport_of(server.config)
    if kind == "sse":
        async with _remote_session(server, auth=auth, force_sse=True) as client:
            yield client
        return

    # ⚠️ THE FALLBACK MUST ONLY COVER OPENING THE TRANSPORT, NEVER THE CALLER'S BODY.
    # This is a generator-based context manager, so an exception raised inside the
    # caller's `async with` block is thrown back in AT the `yield`. Without this
    # flag the handler below caught it, treated a caller-side failure as "streamable
    # HTTP is broken", opened a second connection and yielded again — which either
    # raises `RuntimeError: generator didn't stop after athrow()` or, as measured,
    # replaces the caller's exception TYPE with McpConnectionError. Both hide the
    # real error. `yielded` records that the body already ran, and a failure after
    # that point is re-raised untouched.
    yielded = False
    try:
        async with _remote_session(server, auth=auth) as client:
            yielded = True
            yield client
        return
    except asyncio.CancelledError:
        # A client disconnect. Must propagate — swallowing it into an SSE retry
        # would keep work alive after the request that wanted it is gone, and the
        # blanket `except BaseException` below used to do exactly that.
        raise
    except BaseException as exc:  # noqa: BLE001 — includes anyio ExceptionGroup
        if yielded:
            raise
        if _is_unauthorized(exc) or isinstance(exc, McpAuthRequired):
            raise
        leaf = _unwrap(exc)
        if isinstance(leaf, McpAuthRequired):
            raise leaf from exc
        logger.info(
            "Streamable HTTP failed for MCP server %s (%s); trying SSE.",
            server.name,
            describe_error(exc),
        )
        first_error = exc

    yielded = False
    try:
        async with _remote_session(server, auth=auth, force_sse=True) as client:
            yielded = True
            yield client
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001
        if yielded:
            raise
        if _is_unauthorized(exc) or isinstance(_unwrap(exc), McpAuthRequired):
            raise
        # Report the STREAMABLE HTTP failure, not the SSE one: streamable HTTP is
        # the transport the server was expected to speak, so its error is the
        # diagnostic. The SSE attempt was the long shot.
        raise McpConnectionError(describe_error(first_error)) from exc


def tool_catalogue(tools: Any) -> list[dict]:
    """A `ListToolsResult` reduced to what we cache and hand to the model.

    Only name/description/input_schema are kept. `output_schema`, icons and
    annotations are not sent to the chat completions API, so caching them would
    grow the row for nothing.
    """
    catalogue: list[dict] = []
    for tool in getattr(tools, "tools", []) or []:
        schema = getattr(tool, "input_schema", None)
        catalogue.append(
            {
                "name": tool.name,
                "description": (getattr(tool, "description", None) or "").strip(),
                "input_schema": schema
                if isinstance(schema, dict)
                else {"type": "object", "properties": {}},
            }
        )
    return catalogue


# Ceiling on a single tool result, in characters.
#
# WHY THIS HAS TO EXIST. The value comes from a third-party server and goes straight
# into the NEXT request of the tool loop — appended to `oai_messages` for chat, or
# handed to Deepgram as a FunctionCallResponse for voice. A tool that legitimately
# returns a file, a large query result or a long log would blow the request past the
# model's context limit and fail the whole turn, and on voice it would be read back
# as speech. So it is truncated with an explicit marker: the model is told the result
# was cut rather than being handed a silently partial one, which is the difference
# between "ask for less" and a confidently wrong answer.
#
# ~24k characters is roughly 6k tokens — generous for a tool result, and small next
# to MAX_COMPLETION_TOKENS plus the conversation it shares the window with.
MAX_RESULT_CHARS = 24_000


def _bounded(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    kept = text[:MAX_RESULT_CHARS]
    dropped = len(text) - len(kept)
    return (
        f"{kept}\n\n[truncated: {dropped:,} more characters were returned and "
        f"omitted. Ask for a narrower result if you need the rest.]"
    )


def render_result(result: Any) -> str:
    """A tool result as the text the model will read.

    Structured content wins when present — it is the machine-readable form and
    2.0.0 exposes it as `structured_content`. Otherwise the content blocks are
    flattened: text verbatim, and non-text blocks named rather than dumped, since
    a base64 image inlined into a chat message is thousands of useless tokens.

    An `is_error` result is PREFIXED rather than raised, so the model learns the
    call failed and can react. Same principle as the existing tool bridge in
    app/routers/chat.py: every call gets a result.
    """
    structured = getattr(result, "structured_content", None)
    parts: list[str] = []

    if structured is not None:
        try:
            parts.append(json.dumps(structured, ensure_ascii=False))
        except (TypeError, ValueError):
            parts.append(str(structured))
    else:
        for block in getattr(result, "content", []) or []:
            kind = getattr(block, "type", None)
            if kind == "text":
                parts.append(getattr(block, "text", "") or "")
            elif kind == "resource_link":
                name = getattr(block, "name", "") or ""
                uri = getattr(block, "uri", "") or ""
                parts.append(f"[resource: {name} {uri}]".strip())
            elif kind == "resource":
                resource = getattr(block, "resource", None)
                text = getattr(resource, "text", None)
                parts.append(text if text else f"[resource: {getattr(resource, 'uri', '')}]")
            elif kind in ("image", "audio"):
                mime = getattr(block, "mimeType", None) or getattr(block, "mime_type", "")
                parts.append(f"[{kind} returned{f' ({mime})' if mime else ''}]")

    body = _bounded(
        "\n".join(p for p in parts if p).strip() or "(the tool returned no output)"
    )
    if getattr(result, "is_error", False):
        return f"The tool reported an error: {body}"
    return body
