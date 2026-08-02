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
- **The approval gates are in-process dicts.** The SSE stream that awaits a gate and the
  POST that resolves it are separate requests, so they must hit the same process. The
  deployment is pinned to **min=max=1 replica, single worker** for exactly this reason.
  Scaling out needs a shared rendezvous first.
- **Identity for anything that spends comes from the verified token**, never the request
  body. `ErrandRequest` deliberately does not accept `user_id`/`user_email`.
- **`ENVIRONMENT=production`** is set on the Container App, so the JWT startup guard
  enforces rather than warns.
- **`gpt-5.6` + function tools requires `reasoning_effort="none"`.** The default
  (`medium`) is the unsupported combination. The Deepgram relay carries the same thing
  under its own name, `think.provider.reasoning_mode`.

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
