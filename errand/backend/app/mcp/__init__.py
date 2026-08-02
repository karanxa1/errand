"""Custom MCP servers: the user's own tool providers, and how they authenticate.

A user registers an MCP server (https, or a local subprocess where explicitly
enabled), authorizes it if it needs credentials, and its tools become callable by
the agent on BOTH the chat and voice surfaces.

  config.py    what a config may be; the SSRF guard and the stdio gate
  crypto.py    encryption at rest for every stored credential
  tool_id.py   flat, namespaced, round-trippable tool names for the model
  storage.py   DB-backed TokenStorage so OAuth consent survives a restart
  pending.py   the in-process rendezvous a browser round-trip suspends on
  client.py    transports, lazy 401 discovery, HTTP->SSE fallback, tool calls
  registry.py  the per-user catalogue chat and voice both read

Shaped after two open-source implementations, both of which use the official MCP
SDK: better-chatbot (src/lib/ai/mcp/*, the closer model — lazy auth discovery,
cached tool info, popup callback, namespaced ids) and sparka
(apps/chat/lib/ai/mcp/*, whose `attemptConnection` split between "did it connect"
and "does it need auth" is the shape the status endpoint follows). Where this
diverges from them it is because the Python SDK's OAuth flow keeps `state` and the
PKCE verifier in a local stack frame and offers no hook to adopt either; see
pending.py.
"""
