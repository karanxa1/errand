# Errand — project context

A voice-first agent that runs real purchasing errands and stops for a human before any
money moves. Built for the Agentic Commerce Hackathon. Both halves are deployed and live.

Rules for working in this repo: `AGENTS.md` (this directory). Design law:
`/Users/macbook/.config/opencode/AGENTS.md`. Running list of corrections:
`tasks/lessons.md`.

---

## Shape

```
errand/
  backend/    FastAPI, stateful and long-running (holds a Deepgram WS, streams SSE,
              runs multi-minute tasks) — deliberately not serverless
  frontend/   Next.js 15 App Router, React 19, TypeScript, Tailwind v4, motion
```

**Live:** frontend on Cloudflare Workers (OpenNext + wrangler); backend on Azure
Container Apps; Postgres on Azure Flexible Server. Both CI workflows are path-filtered
off `main`.

---

## Backend

- `app/main.py` — lifespan (JWT startup guard, `init_db`), CORS from `ALLOWED_ORIGINS`,
  mounts the routers, `/api/voice/ws`, legacy `/api/errand/*`.
- `app/routers/auth.py` — register / login / me. bcrypt + JWT bearer.
- `app/routers/conversations.py` — CRUD, `limit`/`offset` bounded on both list endpoints.
- `app/routers/chat.py` — `POST /api/conversations/{id}/chat` (SSE; gpt-5.6 with
  `run_errand` and `web_search` tools) and `/{id}/approve`. Creates the conversation row
  lazily on the first turn, so a client-generated id needs no blocking POST.
- `app/routers/voice.py` — `POST /api/voice/ticket`, the authenticated mint for the
  WebSocket handshake.
- `app/voice/tickets.py` — single-use, 60s, user-bound tickets. Random, never a JWT.
- `app/voice/relay.py` — Deepgram Voice Agent relay and tool bridge. Redeems the ticket
  before anything expensive; closes 4401 if it is missing, stale or replayed.
- `app/orchestrator/run_errand.py` — the persona-agnostic errand engine, emitting
  AuditEvents.
- `app/brokers/` — senso, prava, shopper (Cloudflare Browser Run over CDP), mail
  (AgentMail), linkup. All real; no mock flags set.
- `alembic/` — async env bound to `settings.sqlalchemy_url`.

### Things that are load-bearing and easy to break
- **Approval gates are DB-backed** (table `approvals`, polled by the SSE stream; POST
  `/approve` UPDATEs it). The await and the resolve can now run in different processes.
  The deployment stays pinned to **min=max=1 replica, single worker** anyway, because the
  in-flight `run_errand` coroutine state is still in memory and does not survive a
  restart — this change made the approval *rendezvous* horizontally correct, not the run.
- **Identity for anything that spends comes from the verified token**, never the request
  body. `ErrandRequest` deliberately does not accept `user_id`/`user_email`.
- **`ENVIRONMENT=production`** is set on the Container App, so the JWT startup guard
  enforces rather than warns.
- **`gpt-5.6` (text chat) + function tools requires `reasoning_effort="none"`.** The
  default (`medium`) is the unsupported combination.
- **Voice uses Deepgram-MANAGED providers, not the text models.** `app/voice/relay.py`
  thinks with `anthropic`/`claude-sonnet-5` and speaks with `cartesia`/`sonic-2`, both
  managed (no endpoint, no extra key). `reasoning_mode` is OpenAI-only and is NOT sent.
  Consequence: the sol/terra/luna selector drives the TEXT model only; the voice LLM is
  always claude-sonnet-5. All values are doc-cited in `_settings_message`.
- **The seeded Senso policy names an unroutable vendor** (`demo-pantry.example.com`).
  `run_errand.resolve_merchant` swaps only configured unroutable hosts for the demo
  storefront (`frontend/public/store/`, served on the Worker origin) and emits a
  `context.merchant_resolved` audit event; Senso stays the source of truth for the name,
  budget and rules. Policy extraction keeps restrictive rules ahead of the length cap and
  matches negations generically — a live errand was completing with a banned item before
  both were fixed.

---

## Frontend

Routes: `/` (landing, logged-out), `/login`, `/register`, `/c` (new chat),
`/c/[id]` (one conversation).

- `app/(chat)/layout.tsx` — the persistent shell. Route guard, conversation rail, mobile
  drawer. Being a route-group layout it is a *sibling* of `{children}`, so it never
  unmounts when you move between chats: the list is fetched once per session.
- `app/(chat)/ChatView.tsx` — one conversation. Top bar (model selector + Business /
  Personal toggle — **these stay at the top**, an explicit product decision), thread,
  composer.
- `lib/chatShell.tsx` — the shell context, client-side id generation, path parsing.
- `lib/useChat.ts` — history, the streaming turn, the approval gate. A finished turn is
  **committed locally** into `messages`; it is not re-fetched.
- `lib/useConversations.ts` — the rail's list and its mutations, all local: `insert`,
  `bump`, `patch`, `setTitle`, `remove`. `refresh()` is for the initial load and for
  recovering a failed mutation, not for after every turn.
- `lib/useVoiceAgent.ts` — mints a voice ticket over authenticated HTTP, then opens the
  relay socket with `?ticket=`. Guards against overlapping sessions across the mint await.

### Why a new chat uses `pushState` and not `router.push`
`router.push` unmounts the route subtree, which kills the in-flight SSE reader mid-token.
The first turn mints an id, writes it to the address bar with `window.history.pushState`
(which the App Router picks up for `usePathname` without re-rendering the route), and
streams. Navigation *between* existing chats is a real `router.push`, so browser back
works.

The catch that `pushState` creates: after a first turn, the URL reads `/c/<id>` but the
App Router's *rendered* route is still `/c`, so "New chat" (`router.push("/c")`) is a
no-op — the same `ChatView` is reused and its `activeId` never clears, so the new chat
would not open without a refresh. `ChatView` therefore watches `usePathname()` and, when
the route reads new-chat (`conversationIdFromPath` → null) while it still holds an id
(`shouldResetToNewChat`), blanks itself back to the welcome state. That reset also clears
the previous run's cart/thread. Pinned by `lib/chatShell.newchat.test.ts`.

### Live agentic browser shopping
The LLM can DRIVE the cart instead of the old fixed budget-fill. `run_errand`
takes an optional `shop_decide` (an LLM step: observe → add/remove/done) and
`on_frame` (a live screenshot callback); when both are present and the shopper
implements `agentic_build_cart` (the real browser shopper does; the mock/wallet
shoppers don't, so they cleanly fall back to the classic `build_cart`), the model
builds the cart step by step. The loop lives in
`app/orchestrator/agentic_shop.py` and is driver-agnostic (no Playwright/OpenAI
imports) so it unit-tests against a fake surface; `_PlaywrightShopSurface` in
`app/brokers/shopper.py` drives the real demo DOM. Every add is still checked
against policy (`_is_disallowed`) and the effective budget (`min(policy,
max_cents)`) — the model chooses WHAT, never loosens a rule — and the
authoritative total is still read off `#cart-total`, so every money-path
invariant downstream is unchanged. A user spend cap arrives as `max_cents` on the
`run_errand` tool. Live browser view streams as `browser.frame` SSE frames
(throttled JPEG); the reducer keeps only the latest (`browserFrame`) and the
`BrowserView` card renders it. Design: `docs/superpowers/specs/2026-08-03-live-agentic-browser-design.md`.
Real-merchant agentic shopping (behind `USE_PRAVA_SHOP`) IS now wired:
`PravaShopBroker.agentic_build_cart` lets the LLM choose which real product/
variant to buy from Prava's catalog, bounded by the same policy + budget filter
(it can only pick from allowed, in-budget candidates; a bad pick falls back to
the top-ranked one). The wallet is single-variant per checkout — there is no
multi-item basket — so this is a *select* loop, not the demo store's add/remove.
It drives no page we own, so there is no screenshot: steps stream as audit lines
(rendered as `shop.*` StatusLines), not `browser.frame`s. Unverified live: it
needs the Prava agent linked (`scripts/prava_link.py`, human-approved in the
wallet) and a real card — a live purchase is human-gated and cannot be
auto-tested. Pinned by the agentic tests in `tests/test_prava_wallet.py`.

### Live browser handoff — agent shops, you pay (chat AND voice)
`shop_live` is a tool the model can call to shop a real store in a live Cloudflare
browser and hand the **interactive** view to the human to log in / pay
themselves — the agent enters no card on this path. Backend:
`CloudflareShopperBroker.shop_live_handoff` opens ONE session, runs the agentic
loop to fill the cart, calls `Cloudflare.getLiveView(mode="tab")` for an
interactive `live.browser.run` URL (emitted as a `browser.liveview` SSE frame),
opens `Cloudflare.handoff`, and waits for EITHER Cloudflare's `handoffComplete`
OR the app's own "done paying" signal, pinging keep-alive under the ≤10-min idle
cap. It refuses local targets (no Live View for local Chromium) and is gated by
`use_live_handoff` + Cloudflare creds (`settings.live_handoff_ready`). Frontend:
`LiveViewCard` (Thread.tsx) renders the URL as an iframe (`live.browser.run`
sends `frame-ancestors *`) with an "open in new tab" fallback and a "I've
finished paying" / "Cancel" control wired to the approval resolver. The human's
verdict resolves the SAME approval rendezvous the errand uses.

Parity across chat and voice is enforced: the agentic shop decision lives in
`app/orchestrator/shop_decide.py` (shared by the chat tool loop and the voice
relay), and the voice hook folds `browser.frame`/`browser.liveview` through the
same `applyFrame` reducer, so a voice-driven errand shows the same live browser +
handoff. Not verifiable in CI (needs Cloudflare creds + a human at the payment
step); pinned by `tests/test_live_handoff.py` (readiness gating + local refusal).
Design: `docs/superpowers/specs/2026-08-03-live-view-handoff-design.md`.

### Custom MCP servers — the user's own tools, on both surfaces
A user registers an MCP server and its tools become callable by the agent in chat
AND on a call. Feature lives in `errand/backend/app/mcp/` (`config` validation +
SSRF guard + stdio gate, `crypto` encryption at rest, `tool_id` namespacing,
`storage` DB-backed TokenStorage, `pending` the OAuth rendezvous, `client`
transports, `registry` the per-user catalogue), routed by `app/routers/mcp.py`
(9 routes), tables `mcp_servers` + `mcp_oauth_sessions`. Frontend:
`lib/useMcpServers.ts` + `components/mcp/McpPanel.tsx`, opened from the rail.

**The hot path does no network I/O.** Each server row caches its tool catalogue in
`tools_json`; chat and voice read that (one indexed SELECT) and only an actual
tool INVOCATION opens a connection. Adding servers therefore costs no per-turn
latency. Taken from better-chatbot's `toolInfo` column, which exists for the same
reason. `POST /servers/{id}/refresh` is the only writer.

**Three auth modes.** `none` (open, and a 401 lazily promotes it to `oauth`),
`headers` (a fixed API key / bearer, encrypted at rest), `oauth` (OAuth 2.1 +
PKCE + dynamic registration; tokens persisted so consent survives a restart, and
an expired access token refreshes without a human).

⚠️ **The OAuth rendezvous is IN-PROCESS, and that is forced.** The MCP Python SDK
generates `state` and the PKCE verifier inside a local stack frame and validates
them there, with no storage hook — so better-chatbot's "adopt state from
Postgres" trick cannot port. `POST /authorize` starts the connect as a background
task, `redirect_handler` publishes the URL, and the flow PARKS in
`callback_handler` until `GET /oauth/callback` resolves it (indexed by the SDK's
own `state`, learned off the authorization URL). Same single-worker constraint as
the in-flight `run_errand` and `app/voice/tickets.py` — it adds no NEW limit, but
a future move to multiple workers has to solve all three. Delivery is single-use:
the SDK can re-enter the grant and would replay a spent code.

⚠️ **stdio is OFF by default** (`MCP_ALLOW_STDIO`). A stdio server is a command
this backend spawns, so on a multi-user deployment it is shell access to the
container holding every provider key. better-chatbot allows it and disables it
only on Vercel; the polarity is deliberately inverted here.

⚠️ **User-supplied URLs are SSRF-guarded ON EVERY REQUEST**, not just at
registration. `validate_remote_url` (https only, every resolved address publicly
routable) runs at registration AND inside `_GuardedTransport`, which wraps the
httpx2 transport — because the client follows redirects, so a genuinely public
registered host can answer `302 Location: http://169.254.169.254/...` and land on
Azure IMDS having passed every registration check. The transport also covers OAuth
discovery, dynamic registration and the token exchange, which reach hosts nobody
validated. A resolver failure is FATAL here, the opposite of
`app/prava/validate`'s fail-open reachability check.

⚠️ **Credential encryption is keyed off `JWT_SECRET` unless `MCP_ENCRYPTION_KEY`
is set**, so rotating `JWT_SECRET` orphans stored credentials (recoverable by
re-authorizing, not silent). Set `MCP_ENCRYPTION_KEY` in any deployment that
rotates.

**`MCP_OAUTH_REDIRECT_BASE` must be the backend's public origin** before OAuth
works in a deployment — it defaults to localhost, and the value must match
byte-for-byte across the authorization request and the token exchange. This fails
LOUDLY now rather than at the last step: `Settings.mcp_oauth_redirect_problem` warns
at startup (alongside the JWT guard) and `/api/config` reports `mcp.canSignIn`, so
the panel disables the Sign-in option with the reason instead of offering a control
that can only end in a rejected redirect.

⚠️ **`VoiceSession` carries TWO ids and they are not interchangeable.** `_user_id`
is the SPEND PSEUDONYM (`u_<12 hex>`, matching app.main's derivation so a voice
errand and a typed one attribute spend to the same identity); `_owner_id` is the
real `User.id`. Every ownership query — the MCP catalogue, `call_tool` — must use
`_owner_id`. The first version passed the pseudonym, which matches no row, so voice
silently had an empty MCP catalogue while looking healthy. Pinned by
`test_a_voice_session_looks_up_mcp_servers_by_the_real_user_id`.

⚠️ **The SSE fallback covers opening the transport, never the caller's body.**
`open_session` and `_with_sse_fallback` are generator context managers, so a caller
exception is thrown back in AT the yield; without the `yielded` guard the handler
treated it as a dead transport, opened a second connection and yielded again
(`RuntimeError: generator didn't stop after athrow()`, or a silently replaced
exception type). `asyncio.CancelledError` is re-raised first, so a client disconnect
is never swallowed into a retry. A tool result is capped at
`MAX_RESULT_CHARS` with an explicit truncation marker, because it goes straight into
the next model request.

Tool ids are `mcp__<server>__<tool>`; `__` is refused in server names, which is
what keeps the split exact while letting single underscores through
(`find_customer` stays `find_customer`). Ownership is re-checked on every
resolution, and a row belonging to someone else is 404 rather than 403. Verified
end to end against a real MCP server and a real OAuth authorization server
(`tests/test_mcp_live.py`). Dependency rationale, including why `httpx2` now sits
alongside `httpx`: `errand/docs/decisions/mcp-sdk-dependency.md`.

⚠️ **A third-party tool schema is NORMALISED before it reaches a model API, and
only its ROOT is rewritten** (`app/mcp/schema.py`). JSON Schema permits much more
than a tool parameter slot does, and the mismatch is a 400 on the WHOLE request —
so one odd tool from one server took down every tool in the turn, including the
built-in ones. Measured against the live endpoint rather than inferred: the root
must be exactly `{"type": "object"}` (the LIST form `["object","null"]` is rejected
even though it contains `object`), and `anyOf`/`oneOf`/`allOf`/`enum`/`const`/`not`
are illegal there. Every one of those is legal one level DOWN, so nested schemas are
forwarded untouched — rewriting them would change the tool's contract with its own
server. A root composition is merged rather than truncated to one branch: `allOf`
unions `required`, `anyOf`/`oneOf` INTERSECT it, because a key required by only one
branch is not required in general. `normalise_tool_schema` is total and never
raises. Pinned by `tests/test_mcp_schema.py` (schemas that are cyclic, 40 levels
deep, or not JSON Schema at all).

⚠️ **Healing is layered, and each layer's failure mode is deliberately different.**
  * **Per-tool quarantine** at catalogue build: one unusable cached entry drops
    itself, not the user's whole tool set.
  * **The chat heal ladder** (`app/routers/chat.py`): a tool-shaped 400 re-issues the
    request minus the ONE function the error names, then the next, then all MCP
    tools, capped by `MAX_TOOL_HEAL_ATTEMPTS`. Safe on any pass because `tools` is
    not conversation state — dropping a function does not invalidate messages
    already sent, including results from a function no longer declared. A 400 about
    the conversation (context length, bad messages) is deliberately NOT healed:
    retrying it with fewer tools fails identically and burns the turn twice.
  * **Voice degrades instead of laddering**, because Deepgram accepts functions only
    in the opening Settings message — a rejection there kills the CALL and there is
    no mid-session retry. So the render is guarded and proven JSON-serializable
    before it is sent; on failure the call proceeds with built-in tools only.
  * **Transient retry** (`client.is_transient`, `registry.CALL_ATTEMPTS`): one retry
    for the cold-start case, since most real MCP servers run on platforms that
    suspend when idle. An ANSWER is never retried — a 401 (the user needs the
    Authorize button), an SSRF refusal (a decision our own guard already made, and
    on a flapping DNS record a retry is a second roll of the dice), or a
    deterministic 4xx.
  * **Stale-cache self-heal**: a tool the server no longer has triggers a re-list, so
    the phantom stops being offered instead of failing identically for ever.

⚠️ **`run()` in the chat router declares `nonlocal mcp_catalogue`.** The heal ladder
rebinds it, and without that declaration the assignment makes it a local of the
closure — so the first READ raises `UnboundLocalError` and EVERY turn dies with
"cannot access local variable 'mcp_catalogue'", whether the user has an MCP server
or not. Caught by `tests/test_chat_tool_heal.py` before release; do not remove it
when touching that loop.

### Voice binds the same conversation id as a typed turn
The voice relay socket carries only `model`/`profile`/`ticket` — no conversation id — so a
spoken run is ephemeral. To keep "stop voice, then type" in the *same* chat, both the
first typed turn and opening the mic call one `ensureConversation()` in `ChatView`: it
mints the id + URL once (via `pushState`, so a live session is never torn down) and the
spoken run and the typed turn share it. Without this, voice left `activeId` null and the
first typed message minted a second conversation.

### Styling
Tailwind v4 only. The brand lives in the `@theme` block in `app/globals.css`; there are
no `.module.css` files. See the CSS rules in `AGENTS.md` — in particular that
`postcss.config.mjs` must stay local and must not take a `base` option.

---

## Operating

Secrets live as Azure Container App secrets and GitHub Actions encrypted secrets. The
repo is public and has none. `errand-handoff.md` is gitignored.

Teardown when the hackathon ends:

```
az group delete -n errand-rg --yes
bunx wrangler delete --name errand-frontend
```

Then rotate every sandbox key.
