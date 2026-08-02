"""Custom MCP servers: register, authenticate, inspect.

  GET    /api/mcp/servers              this user's servers (never their secrets)
  POST   /api/mcp/servers              register one, probing it immediately
  PATCH  /api/mcp/servers/{id}         rename / enable / replace credentials
  DELETE /api/mcp/servers/{id}         forget the server and its credentials
  POST   /api/mcp/servers/{id}/refresh reconnect and re-read the tool catalogue
  POST   /api/mcp/servers/{id}/authorize  begin OAuth; returns the URL to open
  GET    /api/mcp/servers/{id}/authorize/{attempt}  poll one attempt's outcome
  POST   /api/mcp/servers/{id}/disconnect drop credentials, keep the server
  GET    /api/mcp/oauth/callback       the authorization server's redirect target

EVERY ROUTE IS SCOPED TO THE AUTHENTICATED USER, and a row belonging to someone
else is reported as 404 rather than 403 — the rule the conversation routes already
follow, so a caller cannot probe which server ids exist. It matters more here: a
server row holds credentials, and its tools are handed to a model that can also
spend money.

The OAuth callback is the one UNAUTHENTICATED route, and it has to be: the
authorization server redirects a browser to it, and that navigation carries no
Authorization header. It is safe because it carries no authority of its own — the
`attempt` id it names is a high-entropy, single-use, expiring token minted for one
(user, server) pair by an authenticated POST, and all the callback can do is hand a
code to the flow already parked on it. See app/mcp/pending.py.
"""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
from app.db import SessionLocal, get_session
from app.mcp import client as mcp_client
from app.mcp import pending, registry, storage
from app.mcp.config import (
    McpConfigError,
    is_stdio,
    redact_config,
    transport_of,
    validate_config,
    validate_headers,
    validate_name,
)
from app.mcp.crypto import encrypt_json, encryption_available
from app.mcp.tool_id import make_tool_id
from app.models import McpServer, User

logger = logging.getLogger("errand.mcp.router")

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


def _require_enabled() -> None:
    if not settings.mcp_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="MCP servers are disabled."
        )


class ToolOut(BaseModel):
    name: str
    # The namespaced id the model actually sees, so the UI can show exactly what
    # the agent was told rather than a name that only looks right.
    tool_id: str
    description: str = ""


class ServerOut(BaseModel):
    id: str
    name: str
    # Redacted: a stdio `env` returns only its key names, and secret headers never
    # come back at all. See app/mcp/config.redact_config.
    config: dict
    transport: str
    auth_mode: str
    enabled: bool
    status: str
    error: str | None = None
    # Which header names are set, so the UI can show that credentials exist
    # without ever returning their values.
    header_names: list[str] = Field(default_factory=list)
    authorized: bool = False
    tools: list[ToolOut] = Field(default_factory=list)
    tools_updated_at: datetime | None = None
    created_at: datetime


class CreateServerRequest(BaseModel):
    name: str = Field(max_length=64)
    # {"url": "...", "transport": "http"|"sse"} or {"command", "args", "env"}.
    config: dict[str, Any]
    auth_mode: Literal["none", "headers", "oauth"] = "none"
    # Only read when auth_mode == 'headers'. Encrypted before it is stored and
    # never returned by any route.
    headers: dict[str, str] | None = None


class UpdateServerRequest(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    auth_mode: Literal["none", "headers", "oauth"] | None = None
    # Present replaces the stored set; absent leaves it untouched. An empty object
    # clears it — that distinction is why this is `| None` rather than defaulted.
    headers: dict[str, str] | None = None
    config: dict[str, Any] | None = None


class AuthorizeStarted(BaseModel):
    attempt_id: str
    authorization_url: str


class AuthorizeStatus(BaseModel):
    state: Literal["pending", "connected", "error", "expired"]
    error: str | None = None
    server: ServerOut | None = None


@dataclass
class _DetachedServer:
    """The fields a connect needs, copied out of the ORM row.

    Exists so a long-parked OAuth flow does not have to hold a database session —
    or a live ORM instance whose session has since closed, which would raise on the
    first lazy attribute access. Structurally compatible with the McpServer
    attributes app/mcp/client.py reads, deliberately and no more than that.
    """

    id: str
    name: str
    config: dict
    auth_mode: str
    secret_headers: str | None


async def _owned(session: AsyncSession, user: User, server_id: str) -> McpServer:
    server = await session.get(McpServer, server_id)
    if server is None or server.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found"
        )
    return server


async def _to_out(server: McpServer) -> ServerOut:
    catalogue = server.tools_json if isinstance(server.tools_json, list) else []
    authorized = (
        await storage.has_tokens(server.id) if server.auth_mode == "oauth" else False
    )
    # A server whose credentials were dropped but whose row still says 'connected'
    # would render as fine while being unusable, so the report is derived here
    # rather than trusted from the column alone.
    reported = server.last_status
    if server.auth_mode == "oauth" and not authorized:
        reported = "authorizing"

    header_names: list[str] = []
    if server.auth_mode == "headers" and server.secret_headers:
        from app.mcp.crypto import decrypt_json

        stored = decrypt_json(server.secret_headers)
        if isinstance(stored, dict):
            header_names = sorted(str(k) for k in stored)

    return ServerOut(
        id=server.id,
        name=server.name,
        config=redact_config(server.config or {}),
        transport=transport_of(server.config or {}),
        auth_mode=server.auth_mode,
        enabled=server.enabled,
        status=reported,
        error=server.last_error,
        header_names=header_names,
        authorized=authorized,
        tools=[
            ToolOut(
                name=str(entry.get("name", "")),
                tool_id=make_tool_id(server.name, str(entry.get("name", ""))),
                description=(entry.get("description") or "")[:300],
            )
            for entry in catalogue
            if isinstance(entry, dict) and entry.get("name")
        ],
        tools_updated_at=server.tools_updated_at,
        created_at=server.created_at,
    )


@router.get("/servers", response_model=list[ServerOut])
async def list_servers(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ServerOut]:
    _require_enabled()
    servers = list(
        await session.scalars(
            select(McpServer)
            .where(McpServer.user_id == user.id)
            .order_by(McpServer.created_at)
        )
    )
    return [await _to_out(s) for s in servers]


@router.post("/servers", response_model=ServerOut, status_code=status.HTTP_201_CREATED)
async def create_server(
    req: CreateServerRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ServerOut:
    """Register a server, then probe it so the user gets a verdict immediately.

    The probe is what turns "saved, good luck" into "connected, 14 tools" or
    "needs authorizing" — and it is also how `auth_mode='none'` gets upgraded to
    'oauth' without the user having to know in advance (better-chatbot's lazy
    discovery). A probe failure does NOT fail the request: the row is kept with
    its error recorded, because a typo is worth fixing in place rather than
    retyping everything.
    """
    _require_enabled()
    try:
        name = validate_name(req.name)
        config = validate_config(req.config)
        headers = validate_headers(req.headers) if req.auth_mode == "headers" else {}
    except McpConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if req.auth_mode == "headers" and not headers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Header authentication needs at least one header.",
        )
    if req.auth_mode == "headers" and not encryption_available():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This deployment cannot store credentials: set MCP_ENCRYPTION_KEY "
                "or JWT_SECRET."
            ),
        )
    if req.auth_mode == "oauth" and is_stdio(config):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A local (stdio) server cannot use OAuth.",
        )

    count = await session.scalar(
        select(func.count())
        .select_from(McpServer)
        .where(McpServer.user_id == user.id)
    )
    if (count or 0) >= settings.mcp_max_servers_per_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"You can register up to {settings.mcp_max_servers_per_user} MCP "
                f"servers. Remove one first."
            ),
        )

    server = McpServer(
        user_id=user.id,
        name=name,
        config=config,
        auth_mode=req.auth_mode,
        secret_headers=encrypt_json(headers) if headers else None,
        enabled=True,
        last_status="unknown",
    )
    session.add(server)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have a server named {name!r}.",
        ) from exc
    await session.refresh(server)

    await _probe(server.id)
    await session.refresh(server)
    return await _to_out(server)


async def _probe(server_id: str) -> None:
    """Connect once, cache the catalogue, and record the verdict. Never raises.

    Runs on its own sessions because it is also called after a commit, where the
    request session may already be gone.

    The row is read into a DETACHED snapshot and the session is closed before the
    connect, for the same reason the OAuth driver does it: a connect can take up to
    CONNECT_TIMEOUT_S, and holding a pooled connection for that — while
    refresh_server_tools opens a SECOND session inside it to write the catalogue —
    is two leased connections per probe for no reason.
    """
    async with SessionLocal() as session:
        row = await session.get(McpServer, server_id)
        if row is None:
            return
        snapshot = _DetachedServer(
            id=row.id,
            name=row.name,
            config=dict(row.config or {}),
            auth_mode=row.auth_mode,
            secret_headers=row.secret_headers,
        )

    try:
        await registry.refresh_server_tools(snapshot)
    except mcp_client.McpAuthRequired:
        # The server answered 401 and we hold no token: record the state AND
        # promote auth_mode, so the UI offers Authorize and the next connect
        # attaches the provider from the start.
        async with SessionLocal() as session:
            row = await session.get(McpServer, server_id)
            if row is not None:
                row.last_status = "authorizing"
                row.last_error = None
                if row.auth_mode == "none":
                    row.auth_mode = "oauth"
                await session.commit()
    except Exception as exc:  # noqa: BLE001 — a probe must not fail the request
        reason = mcp_client.describe_error(exc)
        async with SessionLocal() as session:
            row = await session.get(McpServer, server_id)
            if row is not None:
                row.last_status = "error"
                row.last_error = reason
                await session.commit()
        logger.info("MCP probe failed for %s: %s", server_id, reason)


@router.patch("/servers/{server_id}", response_model=ServerOut)
async def update_server(
    server_id: str,
    req: UpdateServerRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ServerOut:
    _require_enabled()
    server = await _owned(session, user, server_id)

    # Changing the URL invalidates any authorization: a token issued by one
    # server's authorization server is meaningless at another, and keeping it
    # would leave the UI claiming an authorization that cannot work.
    url_changed = False

    try:
        if req.name is not None:
            server.name = validate_name(req.name)
        if req.config is not None:
            new_config = validate_config(req.config)
            url_changed = (server.config or {}).get("url") != new_config.get("url")
            server.config = new_config
        if req.auth_mode is not None:
            server.auth_mode = req.auth_mode
            if req.auth_mode != "headers":
                server.secret_headers = None
        if req.headers is not None:
            headers = validate_headers(req.headers)
            if headers and not encryption_available():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "This deployment cannot store credentials: set "
                        "MCP_ENCRYPTION_KEY or JWT_SECRET."
                    ),
                )
            server.secret_headers = encrypt_json(headers) if headers else None
    except McpConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if req.enabled is not None:
        server.enabled = req.enabled

    if server.auth_mode == "headers" and not server.secret_headers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Header authentication needs at least one header.",
        )

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a server with that name.",
        ) from exc

    if url_changed:
        await storage.clear_tokens(server.id)

    # Re-probe on anything that changes how we connect, so the reported status is
    # never stale relative to the config it describes. A pure enable/disable or
    # rename does not need it.
    if req.config is not None or req.headers is not None or req.auth_mode is not None:
        await _probe(server.id)

    await session.refresh(server)
    return await _to_out(server)


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _require_enabled()
    server = await _owned(session, user, server_id)
    await session.delete(server)  # cascades to its OAuth sessions
    await session.commit()


@router.post("/servers/{server_id}/refresh", response_model=ServerOut)
async def refresh_server(
    server_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ServerOut:
    """Reconnect and re-read the catalogue.

    The user-facing "Test connection". Also the only way a tool ADDED on the
    remote server since registration becomes visible, since the hot path
    deliberately reads cache.
    """
    _require_enabled()
    server = await _owned(session, user, server_id)
    await _probe(server.id)
    await session.refresh(server)
    return await _to_out(server)


@router.post("/servers/{server_id}/disconnect", response_model=ServerOut)
async def disconnect_server(
    server_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ServerOut:
    """Drop stored credentials, keep the server row.

    The registration goes with the tokens — it was minted for a consent that no
    longer exists (app/mcp/storage.clear_tokens).
    """
    _require_enabled()
    server = await _owned(session, user, server_id)
    await storage.clear_tokens(server.id)
    server.last_status = "authorizing" if server.auth_mode == "oauth" else "unknown"
    server.last_error = None
    await session.commit()
    await session.refresh(server)
    return await _to_out(server)


@router.post("/servers/{server_id}/authorize", response_model=AuthorizeStarted)
async def authorize_server(
    server_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AuthorizeStarted:
    """Begin OAuth and return the URL for the browser to open.

    HOW THE HAND-OFF WORKS, because it is not the obvious shape. The SDK's OAuth
    flow is a single coroutine: it discovers metadata, registers a client, builds
    the authorization URL, calls `redirect_handler`, then PARKS in
    `callback_handler` waiting for the code — and it validates `state` and holds
    the PKCE verifier in that stack frame, with no hook to rebuild either
    elsewhere (see app/mcp/pending.py). So the connect is started as a BACKGROUND
    TASK here and left parked; this request only waits for the URL to be
    published, then returns it. The browser visits it, the callback route resolves
    the parked task, and the task finishes the exchange and caches the catalogue.

    The task is deliberately not awaited: awaiting it would mean holding this HTTP
    request open for as long as the human takes to sign in.
    """
    _require_enabled()
    server = await _owned(session, user, server_id)
    if is_stdio(server.config or {}):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A local (stdio) server has nothing to authorize.",
        )
    if not encryption_available():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This deployment cannot store credentials: set MCP_ENCRYPTION_KEY "
                "or JWT_SECRET."
            ),
        )

    # Authorizing is what makes it an OAuth server, so record that before the
    # flow starts — otherwise a reload mid-authorization shows the old mode.
    if server.auth_mode != "oauth":
        server.auth_mode = "oauth"
    server.last_status = "authorizing"
    server.last_error = None
    await session.commit()

    try:
        attempt = pending.start(user.id, server.id)
    except pending.McpAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc

    async def drive() -> None:
        """Hold the parked OAuth flow, then finish and cache the catalogue.

        ⚠️ NO DATABASE SESSION IS HELD ACROSS THE PARK. This coroutine waits for a
        human to sign in — up to AUTH_TIMEOUT_S (5 minutes). An `async with
        SessionLocal()` wrapped around that would lease a pooled connection for the
        whole five minutes, so a handful of people authorizing at once would starve
        the pool and stall every unrelated request. So the server row is read into a
        DETACHED snapshot, the session is closed, and the flow runs against the
        snapshot; the write at the end opens a fresh short-lived session.

        The snapshot is safe to connect with because it only supplies the config,
        the auth mode and the id — none of which can change during the flow without
        a PATCH, and a PATCH re-probes anyway. Credentials are never read from it:
        DbTokenStorage opens its own sessions.
        """
        try:
            async with SessionLocal() as own:
                row = await own.get(McpServer, server_id)
                if row is None:
                    return
                snapshot = _DetachedServer(
                    id=row.id,
                    name=row.name,
                    config=dict(row.config or {}),
                    auth_mode=row.auth_mode,
                    secret_headers=row.secret_headers,
                )
            # Session closed. Everything below can park for minutes.
            await storage.record_state(
                snapshot.id, snapshot.config.get("url", ""), attempt.id
            )
            async with mcp_client.open_session(snapshot, attempt=attempt) as client:
                listed = await client.list_tools()
            catalogue = mcp_client.tool_catalogue(listed)
            async with SessionLocal() as own:
                fresh = await own.get(McpServer, server_id)
                if fresh is not None:
                    fresh.tools_json = catalogue
                    fresh.tools_updated_at = datetime.now(timezone.utc)
                    fresh.last_status = "connected"
                    fresh.last_error = None
                    await own.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            reason = (
                str(exc)
                if isinstance(exc, pending.McpAuthError)
                else mcp_client.describe_error(exc)
            )
            logger.info("MCP authorization failed for %s: %s", server_id, reason)
            async with SessionLocal() as own:
                row = await own.get(McpServer, server_id)
                if row is not None:
                    row.last_status = "error"
                    row.last_error = reason
                    await own.commit()
            # Surface it to whoever is polling this attempt, then let the poll
            # find it before the attempt is dropped.
            pending.fail(attempt.id, reason)
        finally:
            # Give the status poll a moment to observe the outcome before the
            # attempt disappears, so a fast failure is still reportable.
            await asyncio.sleep(30)
            pending.finish(attempt.id)

    task = asyncio.create_task(drive())
    # Hold a reference so the task is not garbage collected mid-flight, and log
    # anything that escaped `drive`'s own handling.
    _AUTH_TASKS.add(task)
    task.add_done_callback(_AUTH_TASKS.discard)

    try:
        url = await pending.wait_for_url(attempt)
    except pending.McpAuthError as exc:
        pending.finish(attempt.id)
        task.cancel()
        async with SessionLocal() as own:
            row = await own.get(McpServer, server_id)
            if row is not None:
                row.last_status = "error"
                row.last_error = str(exc)
                await own.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return AuthorizeStarted(attempt_id=attempt.id, authorization_url=url)


# In-flight authorization drivers. Without a strong reference asyncio may collect
# a running task; same reason app/voice/relay.py tracks its tool tasks.
_AUTH_TASKS: set[asyncio.Task] = set()


@router.get(
    "/servers/{server_id}/authorize/{attempt_id}", response_model=AuthorizeStatus
)
async def authorize_status(
    server_id: str,
    attempt_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AuthorizeStatus:
    """Poll one authorization attempt.

    The popup cannot always reach its opener — a blocked popup, a browser that
    severs `window.opener` on a cross-origin navigation — so the postMessage from
    the callback page is an optimization and THIS is the reliable path. The UI
    polls it either way.
    """
    _require_enabled()
    server = await _owned(session, user, server_id)
    attempt = pending.get(attempt_id)

    if attempt is not None and attempt.user_id != user.id:
        # Not ours to report on. 404, consistent with every other lookup here.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found"
        )

    if server.last_status == "connected":
        return AuthorizeStatus(state="connected", server=await _to_out(server))
    if server.last_status == "error":
        return AuthorizeStatus(
            state="error", error=server.last_error, server=await _to_out(server)
        )
    if attempt is None:
        # Gone without a verdict on the row: the attempt expired.
        return AuthorizeStatus(
            state="expired",
            error="The authorization attempt expired. Try again.",
            server=await _to_out(server),
        )
    return AuthorizeStatus(state="pending", server=await _to_out(server))


# ── the OAuth callback ────────────────────────────────────────────────────────
#
# Shaped after better-chatbot's callback page (src/app/api/mcp/oauth/callback/
# route.ts): a tiny HTML document that postMessages the opener and closes itself,
# so the user is returned to the app without a navigation. The message type
# strings are kept identical to theirs, since the pattern is theirs.

_MSG_SUCCESS = "MCP_OAUTH_SUCCESS"
_MSG_ERROR = "MCP_OAUTH_ERROR"


def _callback_page(*, ok: bool, heading: str, message: str, code: int) -> HTMLResponse:
    """The popup's last page: tell the opener, then close.

    `message` is escaped because it can carry `error_description` straight from
    the authorization server — third-party text interpolated into a document, i.e.
    exactly the shape that is XSS if it is trusted. It is inserted into the DOM
    with textContent rather than markup, and the postMessage payload is built as
    JSON rather than string-concatenated, so neither path can inject script.
    """
    import json as _json

    payload = _json.dumps(
        {"type": _MSG_SUCCESS if ok else _MSG_ERROR, "ok": ok, "message": message}
    )
    redirect = settings.mcp_oauth_success_redirect.strip() if ok else ""
    tone = "#13ef93" if ok else "#ff7a6b"
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(heading)}</title>
<style>
  html,body{{margin:0;height:100%;background:#070b09;color:#cfe3d8;
    font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
  main{{height:100%;display:flex;flex-direction:column;align-items:center;
    justify-content:center;gap:10px;padding:32px;text-align:center}}
  h1{{margin:0;font-size:17px;font-weight:600;color:{tone}}}
  p{{margin:0;max-width:34em;color:#9bb3a6}}
  .dim{{font-size:13px;color:#6d857a}}
</style>
</head>
<body>
<main>
  <h1>{html.escape(heading)}</h1>
  <p id="detail"></p>
  <p class="dim">You can close this window.</p>
</main>
<script>
  var payload = {payload};
  document.getElementById("detail").textContent = payload.message;
  try {{ if (window.opener) window.opener.postMessage(payload, "*"); }} catch (e) {{}}
  {"setTimeout(function(){{ location.replace(" + _json.dumps(redirect) + "); }}, 900);" if redirect else "setTimeout(function(){ window.close(); }, 1200);"}
</script>
</body>
</html>"""
    return HTMLResponse(body, status_code=code)


@router.get("/oauth/callback", include_in_schema=False)
async def oauth_callback(
    request: Request,
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    iss: str | None = Query(default=None),
) -> HTMLResponse:
    """Where the authorization server sends the browser back.

    Unauthenticated by necessity — a redirect carries no bearer token. It holds no
    authority of its own. `state` is the SDK's own high-entropy value for one
    in-flight attempt, bound to one user and one server, single-use and expiring;
    all this route can do is hand a code to the flow already parked on it, and an
    unknown or spent state does nothing at all.

    The CSRF check itself is still the SDK's: the state is passed through unchanged
    and compared inside the flow against the value it generated, so a forged
    callback fails there even if it somehow named a live attempt.
    """
    _require_enabled()

    if error:
        detail = error_description or error
        if state:
            pending.fail_by_state(state, f"The server denied authorization: {detail}")
        return _callback_page(
            ok=False,
            heading="Authorization failed",
            message=detail,
            code=status.HTTP_400_BAD_REQUEST,
        )

    if not code or not state:
        return _callback_page(
            ok=False,
            heading="Authorization failed",
            message="The response was missing its code or state.",
            code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        # `state` here is the SDK's own value, echoed back by the authorization
        # server. It routes to the parked flow AND is handed to the SDK unchanged
        # so the SDK can run its own comparison; this lookup is not the CSRF check.
        pending.resolve(state, code=code, iss=iss)
    except pending.McpAuthError as exc:
        return _callback_page(
            ok=False,
            heading="Authorization failed",
            message=str(exc),
            code=status.HTTP_400_BAD_REQUEST,
        )

    return _callback_page(
        ok=True,
        heading="Connected",
        message="This server is now authorized and its tools are available.",
        code=status.HTTP_200_OK,
    )
