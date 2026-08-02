"""Namespaced tool ids: one flat name the model sees, resolvable back to a call.

The model is handed a single flat list of function names, so a tool from a user's
MCP server has to carry its server with it — two servers can both expose `search`,
and the model needs to be able to say which. This is better-chatbot's
`createMCPToolId` / `extractMCPToolId` (src/lib/ai/mcp/mcp-tool-id.ts) with two
deliberate changes.

CHANGE 1: A DOUBLE SEPARATOR, AND `__` BANNED FROM SERVER NAMES.
better-chatbot joins with a single underscore and splits on the first one, so a
server named `my_tools` with a tool named `run` produces `my_tools_run`, which
parses back as server `my` / tool `tools_run`. Round-trip failures like that
surface as "unknown tool" at call time, on a name the model was told it could use.

Here the separator is `__`, and the SERVER half is guaranteed not to contain it:
`app.mcp.config.validate_name` rejects consecutive underscores in a name, and
sanitization below maps every illegal character to `-` rather than `_`, so it
cannot manufacture one either. Splitting on the FIRST `__` is therefore exact. The
tool half may contain anything, because it is simply everything after that point.

The pay-off is that single underscores survive on both sides: `find_customer`
stays `find_customer` instead of becoming `find_-customer` under a
escape-every-underscore scheme. That matters because the id is what the model
reads and reproduces — a name that matches the server's own naming is one less
thing for it to get wrong.

CHANGE 2: OpenAI's length cap, not the Vercel SDK's.
A function name on /v1/chat/completions must match ^[a-zA-Z0-9_-]{1,64}$. That is
tighter than better-chatbot's 124 (an AI SDK limit), so the budget is split
between the two halves and the join is guaranteed to fit.
https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
"""

from __future__ import annotations

import hashlib
import re

PREFIX = "mcp__"
SEPARATOR = "__"

# OpenAI's ceiling for a function name.
MAX_TOOL_ID_LEN = 64

# What is left for the two halves once the prefix and separator are spent.
_BUDGET = MAX_TOOL_ID_LEN - len(PREFIX) - len(SEPARATOR)

# Reserve room for a disambiguating hash suffix on a truncated half, so two long
# names that share a prefix do not collapse onto one id.
_HASH_LEN = 4

# Anything outside the legal set becomes `-`. Deliberately NOT `_`: mapping to an
# underscore could manufacture the `__` separator inside a half and break the
# split. A space becomes `-` for the same reason.
_ILLEGAL = re.compile(r"[^a-zA-Z0-9_-]")

# Belt to the braces of validate_name: collapse any run of underscores in the
# SERVER half to one. validate_name already refuses `__` in a name, so this only
# fires if that guard is ever loosened — and if it did, a silently broken split
# would be much worse than a slightly altered display name.
_UNDERSCORE_RUN = re.compile(r"_{2,}")


def _clean(value: str) -> str:
    return _ILLEGAL.sub("-", value.strip())


def _clean_server(value: str) -> str:
    return _UNDERSCORE_RUN.sub("_", _clean(value))


def _fit(value: str, limit: int) -> str:
    """Truncate to `limit`, keeping distinct inputs distinct.

    Truncation alone would map `search-repositories-by-topic` and
    `search-repositories-by-owner` to the same id. Appending a short digest of the
    full value keeps them apart; the digest is of the pre-truncation string, so it
    is stable across calls.
    """
    if len(value) <= limit:
        return value
    keep = max(1, limit - _HASH_LEN - 1)
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:_HASH_LEN]
    trimmed = value[:keep]
    # Never leave a trailing underscore against the separator, which would read as
    # a three-underscore run and make the split ambiguous to a human reading logs.
    trimmed = trimmed.rstrip("_")
    return f"{trimmed}-{digest}"


def make_tool_id(server_name: str, tool_name: str) -> str:
    """The flat function name for `tool_name` on `server_name`.

    The TOOL half is given priority in the budget: its name is what tells the model
    what the tool does, whereas the server half is context. A long server name is
    shortened first, and only then the tool name if it still does not fit.
    """
    server = _clean_server(server_name) or "server"
    tool = _clean(tool_name) or "tool"

    if len(server) + len(tool) > _BUDGET:
        # Give the tool up to two thirds, then let the server take what is left,
        # then re-fit the tool against any slack the server did not use.
        tool = _fit(tool, max(8, (_BUDGET * 2) // 3))
        server = _fit(server, max(4, _BUDGET - len(tool)))
        tool = _fit(tool, _BUDGET - len(server))

    return f"{PREFIX}{server}{SEPARATOR}{tool}"


def parse_tool_id(tool_id: str) -> tuple[str, str] | None:
    """Split a tool id back into (server_name, tool_name), or None.

    Returns None for anything not produced by `make_tool_id` — a built-in tool
    name, or a hallucinated one — so a caller can cleanly tell "not an MCP tool"
    from "an MCP tool I cannot find".

    The split is on the FIRST `__` after the prefix, which is exact because the
    server half cannot contain one (see the module docstring).

    NOTE the halves are the SANITIZED forms, and are only byte-identical to the
    originals when neither was truncated. `make_tool_id` is lossy above the length
    cap by necessity, so resolution at call time matches against the ids actually
    GENERATED for this user rather than trusting this parse (see
    app/mcp/registry.py). This is the fast path and the readable one; the registry
    is the authority.
    """
    if not tool_id.startswith(PREFIX):
        return None
    body = tool_id[len(PREFIX) :]
    if SEPARATOR not in body:
        return None
    server, _, tool = body.partition(SEPARATOR)
    if not server or not tool:
        return None
    return server, tool


def is_mcp_tool_id(tool_id: str) -> bool:
    return parse_tool_id(tool_id) is not None
