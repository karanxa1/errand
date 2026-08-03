"""The per-user MCP tool catalogue: one place both chat and voice read from.

Every capability in this repo lands on BOTH surfaces — the chat SSE path and the
Deepgram voice relay — and the two speak different tool dialects (OpenAI
`{"type":"function","function":{...}}` vs Deepgram's flat converse function
definition). So the catalogue is built once here and rendered per surface, the
same way app/orchestrator/shop_decide.py exists to keep one implementation of the
agentic-shop step for both callers.

THE HOT PATH DOES NO NETWORK I/O. `load_tools` reads `tools_json` off the server
rows — the cache written whenever we last connected — so adding MCP servers costs
a single indexed SELECT per turn, not a connect + initialize + tools/list per
server. Only `call_tool` opens a connection. This is the load-bearing half of
better-chatbot's design and the reason its `toolInfo` column exists.

Disabled servers, and servers whose catalogue has never been fetched,
contribute nothing: the model is never told about a tool we cannot actually call.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select

from app.db import SessionLocal
from app.mcp import client as mcp_client
from app.mcp.schema import normalise_tool_schema
from app.mcp.tool_id import make_tool_id
from app.models import McpServer

logger = logging.getLogger("errand.mcp.registry")

# Ceiling on MCP tools handed to the model in one turn. Every tool costs input
# tokens on every request of the tool loop, and a large catalogue measurably
# degrades tool selection — a model choosing between 200 functions picks worse
# than one choosing between 20. Servers are consumed in creation order and the
# cut is reported to the caller so the UI can say so rather than silently
# dropping capability.
MAX_TOOLS = 48

# A description longer than this is truncated. Some servers ship paragraphs of
# prose per tool, which is pure input-token cost on every turn.
MAX_DESCRIPTION_LEN = 480

# How many times a single tool call may be attempted, and the base delay between
# attempts (linear: 0.8s, then 1.6s).
#
# Two, not five. The ceiling is what a user will sit through, not what a server
# might eventually manage: a tool call happens with the model's turn already open
# and the UI showing a call in flight, so every retry is dead air. One retry
# recovers the case this is for — a suspended instance cold-starting — and a server
# that fails twice in a row is down rather than waking up. Only failures
# `mcp_client.is_transient` accepts are retried at all.
CALL_ATTEMPTS = 2
CALL_BACKOFF_S = 0.8


class ServerLike(Protocol):
    """What a connect actually needs off a server row.

    Narrower than `McpServer` on purpose. Callers hand in a DETACHED snapshot
    (app/routers/mcp._DetachedServer) rather than a live ORM instance, so a connect
    — which can park for minutes on an OAuth flow — never holds a pooled database
    connection, and never risks a lazy attribute access against a closed session.
    Stating the contract as a Protocol is what keeps that substitution honest
    instead of relying on duck typing and a misleading type hint.
    """

    id: str
    name: str
    config: dict
    auth_mode: str
    secret_headers: str | None


@dataclass(frozen=True)
class _Snapshot:
    """A `ServerLike` copied out of an ORM row so the session can be released.

    The router has its own identical shape for the same purpose; they are kept
    separate rather than shared because neither module should have to import the
    other, and the whole point of the Protocol above is that the shape is the
    contract.
    """

    id: str
    name: str
    config: dict
    auth_mode: str
    secret_headers: str | None


@dataclass(frozen=True)
class McpTool:
    """One callable tool, already namespaced for the model."""

    tool_id: str
    server_id: str
    server_name: str
    tool_name: str
    description: str
    input_schema: dict


@dataclass(frozen=True)
class McpCatalogue:
    tools: tuple[McpTool, ...]
    # Servers that hold tools but were cut by MAX_TOOLS, so the caller can say so.
    truncated: bool = False

    def by_id(self, tool_id: str) -> McpTool | None:
        """Resolve a tool id the model produced.

        Matched against the ids actually GENERATED for this user rather than by
        parsing the id, because make_tool_id is lossy above the length cap — see
        app/mcp/tool_id.py. This is the authority; the parse is a convenience.
        """
        for tool in self.tools:
            if tool.tool_id == tool_id:
                return tool
        return None

    def openai_tools(self) -> list[dict]:
        """The catalogue as /v1/chat/completions `tools` entries."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.tool_id,
                    "description": tool.description,
                    "parameters": normalise_tool_schema(tool.input_schema),
                },
            }
            for tool in self.tools
        ]

    def deepgram_functions(self) -> list[dict]:
        """The catalogue as Deepgram Voice Agent function definitions.

        Deepgram's `think.functions` entries are flat — name/description/
        parameters at the top level, no `{"type":"function"}` envelope. Mirrors
        the hand-written shapes in app/voice/relay.py `_think_functions`.
        https://developers.deepgram.com/docs/voice-agent-function-calling

        Normalised through the same path as `openai_tools`. Deepgram's THINK is a
        managed Anthropic model rather than OpenAI, so the exact validation rules
        are not identical — but a plain top-level object schema is the shape every
        tool-calling API accepts, and a malformed one here fails the Settings
        message, which kills the CALL rather than one turn. The stricter of the
        two rules is the safe one to apply to both.
        """
        return [
            {
                "name": tool.tool_id,
                "description": tool.description,
                "parameters": normalise_tool_schema(tool.input_schema),
            }
            for tool in self.tools
        ]

    def without_tool(self, tool_id: str) -> "McpCatalogue":
        """This catalogue minus one tool, for the caller's heal ladder.

        See app/routers/chat.py: when a tool-calling API rejects one function
        definition, dropping just that function and retrying keeps every other
        tool — including the other tools on the same server — available for the
        turn. `truncated` is carried through so the prompt note stays accurate.
        """
        return McpCatalogue(
            tools=tuple(t for t in self.tools if t.tool_id != tool_id),
            truncated=self.truncated,
        )

    def server_names(self) -> tuple[str, ...]:
        """Distinct server names, in catalogue order."""
        names: list[str] = []
        for tool in self.tools:
            if tool.server_name not in names:
                names.append(tool.server_name)
        return tuple(names)


async def load_catalogue(user_id: str) -> McpCatalogue:
    """Every enabled tool this user has, from cache. No network I/O.

    Creation order so the set the model sees is stable turn to turn — a tool list
    that reshuffles between passes of the tool loop is a source of confusing model
    behaviour.
    """
    async with SessionLocal() as session:
        servers = list(
            await session.scalars(
                select(McpServer)
                .where(McpServer.user_id == user_id, McpServer.enabled.is_(True))
                .order_by(McpServer.created_at)
            )
        )

    tools: list[McpTool] = []
    truncated = False
    for server in servers:
        catalogue = server.tools_json or []
        if not isinstance(catalogue, list):
            continue
        for entry in catalogue:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            if len(tools) >= MAX_TOOLS:
                truncated = True
                break
            # ONE UNUSABLE ENTRY MUST NEVER COST THE OTHERS. Everything in this
            # block reads a third-party server's cached catalogue, so a name that
            # is not a string, a description that is not text, or a schema that
            # cannot be normalised is a possibility rather than a bug. Each entry
            # is built inside its own guard: a failure drops THAT tool and the
            # loop continues, which is the difference between "one tool is
            # missing" and "this user has no tools".
            try:
                name = str(entry["name"])
                description = str(entry.get("description") or "").strip()
                if len(description) > MAX_DESCRIPTION_LEN:
                    description = description[: MAX_DESCRIPTION_LEN - 1] + "…"
                tools.append(
                    McpTool(
                        tool_id=make_tool_id(server.name, name),
                        server_id=server.id,
                        server_name=server.name,
                        tool_name=name,
                        description=description or f"{name} (via {server.name})",
                        # Normalised HERE, at build time, rather than only when
                        # rendering: a schema that cannot survive normalisation
                        # should cost its own tool now, not fail the request that
                        # renders it. normalise_tool_schema is itself total, so
                        # this is belt-and-braces against a genuinely hostile
                        # value (a cycle, a non-dict) reaching the API.
                        input_schema=normalise_tool_schema(entry.get("input_schema")),
                    )
                )
            except Exception:  # noqa: BLE001 — quarantine the entry, keep the rest
                logger.warning(
                    "Skipping an unusable tool entry from MCP server %s",
                    server.name,
                    exc_info=True,
                )
                continue
        if truncated:
            break

    # A collision can only come from two tools whose ids both truncated to the
    # same string. Dropping the later one is right: the model cannot address an
    # ambiguous name, and silently routing to whichever came first would be worse
    # than the tool being absent.
    seen: set[str] = set()
    unique: list[McpTool] = []
    for tool in tools:
        if tool.tool_id in seen:
            logger.warning(
                "Dropping MCP tool %s/%s: its namespaced id %s collides with an "
                "earlier tool.",
                tool.server_name,
                tool.tool_name,
                tool.tool_id,
            )
            continue
        seen.add(tool.tool_id)
        unique.append(tool)

    return McpCatalogue(tools=tuple(unique), truncated=truncated)


async def refresh_server_tools(server: ServerLike) -> list[dict]:
    """Connect, fetch the catalogue, cache it on the row, and return it.

    The only writer of `tools_json`. Also records the connection verdict, so the
    UI can show real state without a second call.

    Takes a `ServerLike` rather than an `McpServer`: callers pass a detached
    snapshot so no database session is held across the connect (see
    app/routers/mcp._probe). The write at the end opens its own session and looks
    the row up by id, so nothing here depends on the argument being attached.

    Retried on transient failures, like `call_tool`. This is the path behind the
    Test button and behind adding a server, so it is the FIRST thing a user does and
    the most likely to hit a suspended instance cold-starting. Failing it once
    teaches the user their server is broken when it is merely asleep.
    """
    last_error: BaseException | None = None
    for attempt in range(1, CALL_ATTEMPTS + 1):
        try:
            async with mcp_client.open_session(server) as session_client:
                listed = await session_client.list_tools()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < CALL_ATTEMPTS and mcp_client.is_transient(exc):
                delay = CALL_BACKOFF_S * attempt
                logger.info(
                    "Listing tools on MCP server %s failed transiently (%s); "
                    "retrying in %.1fs.",
                    server.name,
                    mcp_client.describe_error(exc),
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            raise
        else:
            catalogue = mcp_client.tool_catalogue(listed)
            await _write_status(
                server.id, status="connected", error=None, tools=catalogue
            )
            return catalogue

    # Unreachable: the loop either returns or re-raises. Present so the function
    # cannot fall through to an implicit None if the bounds above ever change.
    raise last_error if last_error else McpRefreshFailed(server.name)


class McpRefreshFailed(RuntimeError):
    """Raised only if the retry loop above is ever restructured incorrectly."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Could not list tools on {name}.")


async def _write_status(
    server_id: str, *, status: str, error: str | None, tools: list[dict] | None = None
) -> None:
    async with SessionLocal() as session:
        row = await session.get(McpServer, server_id)
        if row is None:
            return
        row.last_status = status
        row.last_error = error
        if tools is not None:
            row.tools_json = tools
            row.tools_updated_at = datetime.now(timezone.utc)
        await session.commit()


async def call_tool(user_id: str, tool_id: str, args: dict) -> str:
    """Invoke an MCP tool on behalf of `user_id` and return text for the model.

    OWNERSHIP IS RE-CHECKED HERE, not inherited from the caller. This is the
    function a tool-calling LLM reaches, with a tool id that came back through a
    model — so it re-derives the catalogue for THIS user and refuses anything not
    in it. A model that hallucinates or replays another user's tool id gets
    "unknown tool", never a call. The SELECT is scoped by user_id, so even a
    correct id belonging to someone else resolves to nothing.

    Every failure returns TEXT rather than raising, because the caller has already
    told the model a call is in flight and must hand back a result for it. The
    same rule the existing tool bridge follows.
    """
    catalogue = await load_catalogue(user_id)
    tool = catalogue.by_id(tool_id)
    if tool is None:
        return (
            f"Unknown tool: {tool_id}. It may have been removed or its server "
            f"disabled."
        )

    # Read what the connect needs, then let the session go: a tool call can run for
    # minutes and must not hold a pooled connection while it does. The ownership
    # re-check happens HERE, inside the session, on the live row — the snapshot that
    # escapes carries no authority of its own.
    async with SessionLocal() as session:
        row = await session.get(McpServer, tool.server_id)
        if row is None or row.user_id != user_id or not row.enabled:
            return f"The server for {tool_id} is no longer available."
        server = _Snapshot(
            id=row.id,
            name=row.name,
            config=dict(row.config or {}),
            auth_mode=row.auth_mode,
            secret_headers=row.secret_headers,
        )

    last_error: BaseException | None = None
    for attempt in range(1, CALL_ATTEMPTS + 1):
        try:
            async with mcp_client.open_session(server) as session_client:
                result = await session_client.call_tool(tool.tool_name, args or {})
        except asyncio.CancelledError:
            # The caller is gone. Must propagate: retrying here would keep work
            # alive after the request that wanted it was abandoned.
            raise
        except mcp_client.McpAuthRequired:
            await _write_status(server.id, status="authorizing", error=None)
            return (
                f"{tool.server_name} needs to be authorized before its tools can be "
                f"used. Ask the user to connect it in the MCP settings panel."
            )
        except Exception as exc:  # noqa: BLE001 — must come back as a tool result
            last_error = exc

            # A stale catalogue: the server no longer has this tool. Retrying the
            # same name cannot help, so re-list instead. That both corrects the
            # cache for the next turn and lets us tell the model something true
            # rather than a transport-shaped lie.
            if mcp_client.is_tool_missing(exc):
                logger.info(
                    "MCP tool %s is gone from %s; refreshing its catalogue.",
                    tool.tool_name,
                    tool.server_name,
                )
                fresh = await _refresh_quietly(server)
                if fresh is not None:
                    names = sorted(
                        str(e.get("name"))
                        for e in fresh
                        if isinstance(e, dict) and e.get("name")
                    )
                    available = ", ".join(names[:12]) or "none"
                    return (
                        f"{tool.server_name} no longer has a tool called "
                        f"{tool.tool_name}. Its tools are now: {available}. "
                        f"The list has been refreshed, so try again."
                    )
                break

            if attempt < CALL_ATTEMPTS and mcp_client.is_transient(exc):
                delay = CALL_BACKOFF_S * attempt
                logger.info(
                    "MCP tool %s failed transiently (%s); retrying in %.1fs "
                    "(attempt %d/%d).",
                    tool_id,
                    mcp_client.describe_error(exc),
                    delay,
                    attempt + 1,
                    CALL_ATTEMPTS,
                )
                await asyncio.sleep(delay)
                continue

            break
        else:
            # A successful call is also the freshest evidence the server is
            # reachable. Recorded even when an earlier attempt failed, so a cold
            # start does not leave the row reading "error".
            await _write_status(server.id, status="connected", error=None)
            return mcp_client.render_result(result)

    reason = mcp_client.describe_error(last_error) if last_error else "unknown error"
    await _write_status(server.id, status="error", error=reason)
    logger.warning("MCP tool %s failed: %s", tool_id, reason)
    return f"The tool {tool.tool_name} on {tool.server_name} failed: {reason}"


async def _refresh_quietly(server: ServerLike) -> list[dict] | None:
    """Re-list a server's tools, returning None rather than raising.

    Used on the stale-catalogue path, where we are already handling one failure and
    a second must not replace it with a less informative one.
    """
    try:
        return await refresh_server_tools(server)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not refresh the catalogue for MCP server %s", server.name,
            exc_info=True,
        )
        return None


def tool_prompt_note(catalogue: McpCatalogue) -> str:
    """A sentence for the system prompt naming the user's connected servers.

    Without this the model sees a set of oddly-prefixed function names with no
    idea they are the user's own integrations. Naming the servers is what makes it
    reach for them, and it is cheap — one line regardless of tool count.
    """
    if not catalogue.tools:
        return ""
    listed = ", ".join(catalogue.server_names())
    note = (
        f"\nThe user has connected these tool servers: {listed}. Their tools are "
        f"the functions prefixed `mcp__`. Use them when the request matches what "
        f"they do, and say which server you used."
    )
    if catalogue.truncated:
        note += (
            f" (Only the first {MAX_TOOLS} tools are loaded; some are not "
            f"available this turn.)"
        )
    return note
