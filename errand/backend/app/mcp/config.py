"""MCP server config: shapes, validation, and the SSRF guard.

A config is SHAPE-DISCRIMINATED rather than carrying a type label — a `url` key
means a remote server, a `command` key means a local subprocess. That is
better-chatbot's `isMaybeRemoteConfig` / `isMaybeStdioConfig` split
(src/lib/ai/mcp/is-mcp-config.ts), and the reason to copy it is that a
self-describing config cannot disagree with its own label the way a separate
`type` column can.

Two things here are security boundaries rather than validation niceties.

1. THE URL IS FETCHED BY THE SERVER, NOT THE BROWSER.
   A user-supplied URL that our backend then requests is a server-side request
   forgery primitive. Left open it reaches the cloud instance metadata endpoint
   (169.254.169.254 — on Azure, IMDS), the container's own loopback, and every
   private range routable from inside the VNet. This backend holds live payment
   credentials, so that is not a theoretical concern. `validate_remote_url`
   therefore requires https and refuses any host that resolves into a
   non-public range, checking EVERY address the name resolves to.

2. STDIO IS ARBITRARY CODE EXECUTION, AND IS OFF BY DEFAULT.
   A stdio server is a command this backend spawns. In a multi-user deployment,
   accepting one from a user is handing them a shell — `sh -c '…'` reaches the
   process environment, which holds the OpenAI, Cloudflare, Prava and Deepgram
   keys. better-chatbot allows stdio and switches it OFF for Vercel
   (IS_MCP_SERVER_REMOTE_ONLY); that default is right for a self-hosted,
   single-operator instance and wrong here, so the polarity is inverted: stdio is
   refused unless MCP_ALLOW_STDIO is explicitly true, and it is documented as a
   single-operator-only setting.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any, Literal
from urllib.parse import urlparse

from app.config import settings

# Transports the remote path can speak. Streamable HTTP is the current standard
# and is tried first; SSE is the superseded transport kept as a fallback because
# plenty of deployed servers still only offer it. The MCP Python SDK ships both
# (mcp.client.streamable_http / mcp.client.sse) and its own docs advise against
# building anything new on SSE.
# https://py.sdk.modelcontextprotocol.io/v2/client/transports/
TransportKind = Literal["http", "sse", "stdio"]

AUTH_MODES = ("none", "headers", "oauth")

# A server name is the human half of a namespaced tool id, so it is constrained
# to what survives that: letters, digits, spaces, and the separators that
# sanitize cleanly. Length is bounded well under the column so a name can never
# be the reason a tool id needs truncating.
_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9 ._-]{0,47}\Z")

# Consecutive underscores are refused because `__` is the tool-id separator, and
# the split relies on the SERVER half never containing one — that is what lets
# single underscores survive unescaped in a tool name (`find_customer` stays
# `find_customer`). See app/mcp/tool_id.py.
_DOUBLE_UNDERSCORE = re.compile(r"__")

# Header names we refuse to let a user set. Authorization is managed by the auth
# mode (a static bearer belongs in `headers` mode, an OAuth token is minted), and
# the MCP-specific headers are owned by the transport — letting a user pin
# either produces failures that look like server bugs. The Host header is a
# request-routing control and not a credential at all.
_RESERVED_HEADERS = frozenset(
    {"host", "content-length", "content-type", "accept", "mcp-session-id",
     "mcp-protocol-version", "connection", "transfer-encoding"}
)


class McpConfigError(ValueError):
    """A config we refuse, with a message written for whoever has to fix it."""


def is_remote(config: dict[str, Any]) -> bool:
    return isinstance(config, dict) and isinstance(config.get("url"), str)


def is_stdio(config: dict[str, Any]) -> bool:
    return isinstance(config, dict) and isinstance(config.get("command"), str)


def transport_of(config: dict[str, Any]) -> TransportKind:
    """Which transport this config describes.

    A remote config may pin `transport: 'sse'` to skip straight to SSE, for a
    server known not to speak streamable HTTP; the default is 'http', which
    falls back to SSE on its own (see app/mcp/client.py).
    """
    if is_stdio(config):
        return "stdio"
    kind = (config.get("transport") or "http").strip().lower()
    return "sse" if kind == "sse" else "http"


def validate_name(name: str) -> str:
    value = (name or "").strip()
    if not _NAME_RE.match(value):
        raise McpConfigError(
            "Server name must start with a letter or digit and contain only "
            "letters, digits, spaces, dots, dashes or underscores (max 48)."
        )
    if _DOUBLE_UNDERSCORE.search(value):
        raise McpConfigError(
            "Server name cannot contain two underscores in a row — that sequence "
            "separates the server from the tool in the name the agent sees. Use a "
            "single underscore, a dash or a space."
        )
    return value


def _is_public_address(raw: str) -> bool:
    """Whether `raw` is a global-scope address we are willing to fetch.

    `is_global` is the check that matters — it excludes loopback, link-local
    (which is where 169.254.169.254 lives), private ranges, CGNAT, multicast and
    the reserved blocks in one go, for both IPv4 and IPv6. Explicitly rejecting
    the IPv4-mapped form too, since ::ffff:127.0.0.1 is global by the v6 rules
    while being loopback in effect.
    """
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        return mapped.is_global
    return addr.is_global


def validate_remote_url(url: str) -> str:
    """Return the URL, or raise if it is not a safe public https endpoint.

    Rejects, in order: a missing or non-https scheme, a missing host, a host that
    is a literal non-public IP, and a hostname that RESOLVES to any non-public
    address. The last check is the one that matters and the one that is easy to
    forget — `internal.example.com` pointing at 10.0.0.5 is a public-looking name
    for a private target, and DNS rebinding aside, resolving here is what closes
    the obvious hole.

    A resolver failure is fatal rather than inconclusive: this is a security
    check, so "I could not tell" must read as no. That is the opposite of
    app/prava/validate.assert_resolvable, which is a reachability check on a value
    we are SENDING and correctly fails open. The asymmetry is the point.
    """
    value = (url or "").strip()
    if not value:
        raise McpConfigError("Server URL is required.")
    if len(value) > 2000:
        raise McpConfigError("Server URL is too long.")
    parsed = urlparse(value)
    if not parsed.scheme:
        raise McpConfigError(
            f"Server URL is missing its scheme: {value!r} (expected https://…)"
        )
    if parsed.scheme == "http":
        # Allowed only where a developer is deliberately pointing at a server on
        # their own machine, which is also the one case the loopback check below
        # would otherwise refuse. Never in a deployment.
        if not settings.mcp_allow_insecure_http:
            raise McpConfigError(
                "Server URL must use https. Plain http is refused because the "
                "access token would cross the network in clear text. (Set "
                "MCP_ALLOW_INSECURE_HTTP=true for local development only.)"
            )
    elif parsed.scheme != "https":
        raise McpConfigError(
            f"Server URL must use https, got {parsed.scheme!r} in {value!r}."
        )

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise McpConfigError(f"Server URL has no host: {value!r}")

    # With the http escape hatch on, a developer pointing at their own machine is
    # the entire point, so the private-range checks below are what we are being
    # asked to skip. Kept to exactly that combination — it cannot be reached in a
    # deployment, where mcp_allow_insecure_http is false.
    if settings.mcp_allow_insecure_http:
        return value

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not _is_public_address(host):
            raise McpConfigError(
                f"Server URL points at a non-public address ({host}). Only "
                f"publicly routable hosts are allowed."
            )
        return value

    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise McpConfigError(
            f"Server host {host!r} does not resolve. Check the URL."
        ) from exc
    except OSError as exc:
        raise McpConfigError(f"Could not resolve server host {host!r}.") from exc

    resolved = {info[4][0] for info in infos}
    if not resolved:
        raise McpConfigError(f"Server host {host!r} does not resolve. Check the URL.")
    for address in resolved:
        if not _is_public_address(address):
            raise McpConfigError(
                f"Server host {host!r} resolves to a non-public address "
                f"({address}). Only publicly routable hosts are allowed."
            )
    return value


def validate_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Return a cleaned header map, or raise on anything we will not send."""
    if not headers:
        return {}
    if len(headers) > 20:
        raise McpConfigError("At most 20 headers.")
    cleaned: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = (raw_name or "").strip()
        value = (raw_value or "").strip()
        if not name:
            continue
        if not re.match(r"\A[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}\Z", name):
            # RFC 9110 token characters. A header name with a newline in it is
            # request splitting, which is why this is a whitelist.
            raise McpConfigError(f"Header name is not valid: {raw_name!r}")
        if name.lower() in _RESERVED_HEADERS:
            raise McpConfigError(
                f"The {name} header is managed by the transport and cannot be set "
                f"here. For a bearer token or API key, use any other header name "
                f"the server expects."
            )
        if len(value) > 4096 or "\n" in value or "\r" in value:
            raise McpConfigError(f"Header value for {name} is not valid.")
        cleaned[name] = value
    return cleaned


def validate_stdio(config: dict[str, Any]) -> dict[str, Any]:
    """Return a stdio config, or raise. Refused entirely unless opted in.

    See the module docstring: this spawns a process inside the backend, whose
    environment holds every provider key the deployment has.
    """
    if not settings.mcp_allow_stdio:
        raise McpConfigError(
            "Local (stdio) MCP servers are disabled on this deployment. A stdio "
            "server runs a command inside the backend, which would expose its "
            "environment — including provider API keys — to whoever registered "
            "it. Use a remote https server instead, or set MCP_ALLOW_STDIO=true "
            "if you are the only operator of this instance."
        )
    command = (config.get("command") or "").strip()
    if not command:
        raise McpConfigError("Command is required for a local server.")
    args = config.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise McpConfigError("Command args must be a list of strings.")
    env = config.get("env") or {}
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise McpConfigError("Command env must be a map of strings.")
    return {"command": command, "args": args, "env": env}


def validate_config(config: Any) -> dict[str, Any]:
    """Normalize and validate a submitted config, or raise McpConfigError.

    Note `headers` is NOT part of the stored config: a header map is where a
    bearer token lives, and the config column is plain JSON. Secrets travel via
    `secret_headers`, encrypted (app/mcp/crypto.py), so a config that arrives
    with headers on it has them stripped here and re-attached at connect time.
    """
    if not isinstance(config, dict):
        raise McpConfigError("Config must be an object.")
    if is_stdio(config):
        return validate_stdio(config)
    if is_remote(config):
        url = validate_remote_url(config["url"])
        kind = transport_of(config)
        return {"url": url, "transport": kind}
    raise McpConfigError(
        "Config must contain either a `url` (remote server) or a `command` "
        "(local server)."
    )


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """The config as it is safe to return to a client.

    A stdio `env` map is where an API key for the child process would be put, so
    its VALUES are dropped and only the key names are returned — enough for the
    UI to show what is configured without handing the secret back out. (A client
    that just wrote the value already has it; a shared or later session does not.)
    """
    if is_stdio(config):
        env = config.get("env") or {}
        return {
            "command": config.get("command", ""),
            "args": list(config.get("args") or []),
            "envKeys": sorted(env.keys()),
        }
    return {
        "url": config.get("url", ""),
        "transport": transport_of(config),
    }
