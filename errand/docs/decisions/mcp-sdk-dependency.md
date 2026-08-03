# Why custom MCP servers use the official SDK, and carry `httpx2`

Decided 2026-08-03, while adding user-registered MCP servers (`app/mcp/`).

## The dependency

`mcp>=2.0.0`. It brings `httpx2`, `httpcore2`, `mcp-types`, `jsonschema`,
`opentelemetry-api`, `sse-starlette` and `truststore` — twelve packages including
transitive ones.

**`httpx2` is a separate distribution from `httpx`, not a major version of it.**
The backend already depends on `httpx>=0.28.1` for every broker (Prava, Senso,
AgentMail, Linkup). Both are now installed. `uv` resolves this cleanly because
they are different names on PyPI, so there is no version conflict to resolve —
but there are genuinely two HTTP stacks in the image.

## Why not hand-roll the protocol

The MCP wire protocol over streamable HTTP is not the hard part. It is JSON-RPC
over POST with an `Mcp-Session-Id` header and SSE framing on the response —
perhaps 200 lines.

OAuth 2.1 is the hard part, and it is where the security lives:

- protected-resource metadata discovery (`/.well-known/oauth-protected-resource`)
- authorization-server metadata discovery, with the OIDC fallback path
- `WWW-Authenticate` parsing to find the resource metadata URL
- dynamic client registration (RFC 7591), including judging whether the
  registration the server returned is one this flow can actually use
- PKCE generation and verification
- `state` generation and comparison (CSRF)
- issuer validation on the authorization response (RFC 9207, server mix-up)
- the `resource` parameter (RFC 8707), and when it may be sent
- token refresh, and re-authorization when a refresh is rejected
- Client ID Metadata Documents, which the 2026-07-28 spec revision introduces as
  the replacement for dynamic registration

Each of those is a place to introduce a security bug, several are subtle, and the
list is still moving. Owning that implementation permanently is a worse trade than
carrying a second HTTP library.

Both reference implementations reached the same conclusion: `better-chatbot` uses
`@modelcontextprotocol/sdk`, `sparka` uses `@ai-sdk/mcp`. Neither hand-rolls it.

## Why two HTTP stacks is tolerable here

Checked rather than assumed:

- **Imports are unambiguous.** `app/mcp/client.py` is the only module that
  imports `httpx2`. Every broker imports `httpx`. Verified by grep; there is no
  file that uses both.
- **Pools are separate and correctly scoped.** Each MCP operation builds its own
  `httpx2.AsyncClient` inside an `async with`, so it is closed on every exit path.
  It is not a long-lived pool competing with the brokers'.
- **No event-loop interaction.** Both are plain asyncio clients with no global
  state and no loop-binding.

## The one real difference: TLS trust

`mcp` pulls in `truststore`, so `httpx2` validates against the **OS trust store**.
`httpx` validates against **`certifi`'s bundled roots**.

Consequence: a host whose certificate chains to a CA installed on the machine (a
corporate root, an internal CA) verifies for an MCP call and may fail for a broker
call, and a host trusted by `certifi` but not by the OS fails the other way. This
is not a defect — arguably the OS store is the better default — but it means
"TLS works for one and not the other" is a real, explicable state rather than a
mystery.

## The trap to remember

```python
except httpx.HTTPError:   # will NOT catch an MCP transport failure
except httpx2.HTTPError:  # what app/mcp/client.py catches
```

The hierarchies are disjoint. `app/mcp/client.py` names the `httpx2` types
deliberately. Any future attempt to unify error handling across the broker and MCP
paths has to handle both, and a single `except httpx.*` there would silently stop
catching MCP failures — a change that passes review by looking correct.

## Costs accepted

- Image size and CVE surface: twelve more packages, two HTTP stacks to patch.
- A reader hitting `import httpx2` will assume a typo. Flagged at that import and
  in the module docstring.

## The exit, if it stops being worth it

Migrate the brokers to `httpx2` and drop `httpx` entirely, rather than trying to
push the SDK onto `httpx`. The APIs are close enough that it is mostly mechanical.
Not worth doing pre-emptively: the brokers work, and churning five integration
modules to remove one dependency is a poor trade today.
