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

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select

from app.db import SessionLocal
from app.mcp import client as mcp_client
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
                    "parameters": _sanitize_schema(tool.input_schema),
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
        """
        return [
            {
                "name": tool.tool_id,
                "description": tool.description,
                "parameters": _sanitize_schema(tool.input_schema),
            }
            for tool in self.tools
        ]


def _sanitize_schema(schema: dict) -> dict:
    """A JSON Schema an LLM tool parameter slot will accept.

    Two fixes, both from real MCP servers rather than hypotheses:
      * A missing or non-object schema becomes an empty object schema. Servers do
        ship tools with `null` input schemas, and a null `parameters` is a 400.
      * `$schema` and `title` are dropped at the top level. They are inert for
        tool calling and only add tokens.
    """
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}
    cleaned = {k: v for k, v in schema.items() if k not in ("$schema", "title")}
    cleaned.setdefault("type", "object")
    if cleaned["type"] == "object":
        cleaned.setdefault("properties", {})
    return cleaned


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
            name = str(entry["name"])
            description = (entry.get("description") or "").strip()
            if len(description) > MAX_DESCRIPTION_LEN:
                description = description[: MAX_DESCRIPTION_LEN - 1] + "…"
            tools.append(
                McpTool(
                    tool_id=make_tool_id(server.name, name),
                    server_id=server.id,
                    server_name=server.name,
                    tool_name=name,
                    description=description or f"{name} (via {server.name})",
                    input_schema=entry.get("input_schema") or {},
                )
            )
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
    """
    async with mcp_client.open_session(server) as session_client:
        listed = await session_client.list_tools()
    catalogue = mcp_client.tool_catalogue(listed)
    await _write_status(
        server.id, status="connected", error=None, tools=catalogue
    )
    return catalogue


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

    try:
        async with mcp_client.open_session(server) as session_client:
            result = await session_client.call_tool(tool.tool_name, args or {})
    except mcp_client.McpAuthRequired:
        await _write_status(server.id, status="authorizing", error=None)
        return (
            f"{tool.server_name} needs to be authorized before its tools can be "
            f"used. Ask the user to connect it in the MCP settings panel."
        )
    except Exception as exc:  # noqa: BLE001 — must come back as a tool result
        reason = mcp_client.describe_error(exc)
        await _write_status(server.id, status="error", error=reason)
        logger.warning("MCP tool %s failed: %s", tool_id, reason)
        return f"The tool {tool.tool_name} on {tool.server_name} failed: {reason}"

    # A successful call is also the freshest evidence the server is reachable.
    await _write_status(server.id, status="connected", error=None)
    return mcp_client.render_result(result)


def tool_prompt_note(catalogue: McpCatalogue) -> str:
    """A sentence for the system prompt naming the user's connected servers.

    Without this the model sees a set of oddly-prefixed function names with no
    idea they are the user's own integrations. Naming the servers is what makes it
    reach for them, and it is cheap — one line regardless of tool count.
    """
    if not catalogue.tools:
        return ""
    names: list[str] = []
    for tool in catalogue.tools:
        if tool.server_name not in names:
            names.append(tool.server_name)
    listed = ", ".join(names)
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
