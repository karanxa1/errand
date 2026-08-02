# Errand — Deployment & Feature Build Progress

## Live URLs
- Frontend: https://errand-frontend.rough-cell-383c.workers.dev (Cloudflare Workers via OpenNext)
- Backend: https://errand-backend.orangestone-061a2c55.eastus.azurecontainerapps.io (Azure Container Apps)
- Repo (PUBLIC, no secrets): https://github.com/karanxa1/errand
- Git root: /Users/macbook/prava (branch main). App in errand/. .env gitignored.

## Azure resources (RG: errand-rg)
- Container App: errand-backend (env errand-env), ACR: ca2ea22877c1acr (admin enabled)
- Postgres Flexible Server: **errand-pg-probe** in **centralus** (NOT errand-pg — eastus/eastus2 were capacity-restricted)
  - admin user: errandadmin, db: errand, public access enabled, firewall: allow-azure-services (0.0.0.0) + my-dev-ip
  - DATABASE_URL saved: /tmp/errand-dburl.txt (postgresql+asyncpg://errandadmin:PASS@errand-pg-probe.postgres.database.azure.com:5432/errand?ssl=require)
  - db password: /tmp/errand-dbpass.txt ; JWT secret: /tmp/errand-jwt.txt
- Migration ALREADY RAN against prod Postgres (alembic upgrade head) — tables exist: users, conversations, messages, alembic_version

## CI (GitHub Actions, both were green before this feature wave)
- .github/workflows/backend.yml: az acr build -> az containerapp update -> health smoke
- .github/workflows/frontend.yml: bun install -> opennextjs-cloudflare build -> wrangler deploy -> smoke
- 16 secrets set incl AZURE_CREDENTIALS, ACR_*, CLOUDFLARE_*, NEXT_PUBLIC_BACKEND_URL, app keys.
- **CI STILL NEEDS: backend.yml must run `alembic upgrade head` (migration) before/after deploy, and DATABASE_URL + JWT_SECRET added as GH secrets + set on the app.**

## What was built THIS wave (committed as c388225)
Backend (all verified live on Postgres locally):
- deps added: sqlalchemy[asyncio], asyncpg, aiosqlite, alembic, bcrypt, pyjwt, python-multipart
- app/config.py: database_url, jwt_secret/jwt_alg/jwt_expire_minutes, sqlalchemy_url property, allowed_origins/cors_origins
- app/db.py: async engine (sqlite dev / postgres prod), Base, get_session, init_db
- app/models.py: User, Conversation, Message (JSON events col for tool runs)
- app/auth.py: bcrypt hash/verify, JWT create/decode, get_current_user dep
- app/routers/auth.py: POST /api/auth/register, /login, GET /me
- app/routers/conversations.py: CRUD /api/conversations (+ /{id})
- app/routers/chat.py: POST /api/conversations/{id}/chat (SSE, gpt-5.6 + run_errand + web_search tools, reasoning_effort="none" REQUIRED), POST /{id}/approve
- app/main.py: lifespan(init_db), include 3 routers
- alembic/ (async env.py wired to settings.sqlalchemy_url + Base.metadata, render_as_batch), migration 17d0d0b91fb9, scripts/migrate.sh
- Voice relay keepalive fix (earlier): app/voice/relay.py KeepAlive every 5s + ping_timeout=None + close-code surfacing

Frontend (subagent, tsc + next build passed, screenshots verified on-brand):
- lib/auth.tsx (AuthProvider/useAuth, localStorage errand_token), lib/useChat.ts, lib/useConversations.ts
- app/login/page.tsx, app/register/page.tsx (+css), components/Sidebar.tsx (+css), components/auth/
- app/page.tsx rebuilt: sidebar + chat thread + composer; **model selector + Business/Personal toggle MOVED TO TOP header** (verified in screenshot)
- app/layout.tsx wraps AuthProvider

## BACKEND DEPLOY: DONE + VERIFIED LIVE
Backend on Azure Postgres fully works: register/login/conversation/streaming chat/auto-title/persist/reload all confirmed on live URL. (Routes were just slow to warm on first check, not a cache bug.)
DATABASE_URL + JWT_SECRET set as ACA secrets (secretref). ALLOWED_ORIGINS = frontend workers.dev (set earlier).

## REMAINING TODO
1. Fix backend deploy: build+push UNIQUE-tagged image, update app, confirm new routes live + register works on live Postgres.
2. Set ALLOWED_ORIGINS (frontend workers.dev) still correct on app (was set earlier).
3. Redeploy frontend (already built w/ backend URL; new auth/chat code committed) via wrangler — CI will do it, or manual.
4. Update backend.yml CI to: build unique tag, run migration (alembic upgrade head against DATABASE_URL), deploy. Add DATABASE_URL + JWT_SECRET GH secrets.
5. Push commit c388225 to trigger CI (frontend + backend). Verify both green.
6. End-to-end verify on LIVE urls: register -> login -> new chat -> send msg (stream) -> history persists -> reload.
7. Delete this MD file when done.

## Local test servers (kill when done)
- backend :8787 (was running on Postgres), frontend :3000 dev. Test DBs removed.

## Key facts
- uv: MUST export UV_CACHE_DIR=/Users/macbook/prava/errand/backend/.uvcache
- frontend: bun, workdir errand/frontend
- gpt-5.6 tool calls REQUIRE reasoning_effort="none" on /v1/chat/completions
- anti-slop law at /Users/macbook/.config/opencode/AGENTS.md (green-black, Gambarino, #13EF93, no purple/emoji)
