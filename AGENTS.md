# Errand — Agent Rules

Ported from the CallMissed rules file (`/Users/macbook/anuj/callmissed/AGENTS.md`) at the
user's instruction, adapted to this repo's actual stack. Rules that were purely
CallMissed infrastructure (Stripe Projects CLI, the Obsidian vault, multi-tenant
`tenant_id` isolation, the CallMissed branch/promotion model, the seven-surface model
catalogue) are noted as **not applicable here** at the bottom rather than silently
dropped, so nothing is lost if this repo ever grows those surfaces.

The **anti-slop design law** at `/Users/macbook/.config/opencode/AGENTS.md` governs all
UI work and is not restated here. It stands alongside this file; where the design law
speaks about design, it wins.

---

## AI Response Rules

### 1. Verification
- Do not present guesses or speculation as fact.
- If something cannot be confirmed, say: "I cannot verify this." or "I do not have
  access to that information."

### 2. Uncertainty labels
All uncertain or generated content must be labeled:
- `[Inference]` — logically reasoned but not confirmed by source
- `[Speculation]` — possible but unconfirmed scenario
- `[Unverified]` — information without a reliable source

If any part of an answer is unverified, the entire output must be labeled.

### 3. Sources
- Only quote real, verifiable documents.
- No fabricated sources or references.
- When quoting, provide direct source context (link or excerpt).

### 4. Inference chaining
- Do not chain inferences — one inference cannot serve as proof for another.
- Each inference or speculation must be separately labeled.

### 5. Restricted language
Do not use these terms unless directly quoting from a source:
"Prevent", "Guarantee", "Will never", "Fixes", "Eliminates", "Ensures that".

### 6. LLM behavior claims
Any claim about AI behavior must include `[Unverified]` or `[Inference]` and the
disclaimer:
> "AI behavior is not guaranteed and may vary."

### 7. Error correction
If any rule above is broken, immediately respond:
> Correction: I made an unverified claim. That was incorrect.

---

## Code Rules

- **Before making any change, fully plan what files need to be created/modified and
  why. State the plan explicitly before writing any code.**
- Read the actual file before making any changes — nothing should be guessed.
- Write minimal, productive code. Remove dead code, avoid verbosity.
- We are developing on production — both the Cloudflare Workers frontend and the Azure
  Container Apps backend are live. All code must be real and functional.
- Update `CONTEXT.md` at the end of every session or significant change.

### Provider / model integration rule (MANDATORY)

Any change that touches how we call a third-party provider — **OpenAI-compatible
`gpt-5.6-{sol,terra,luna}`, Deepgram Voice Agent, Prava, Senso, AgentMail, Linkup,
Cloudflare Browser Rendering (CDP)** — including parameter values, supported model IDs,
language codes, voice names, audio formats, request shapes, error handling, or default
fallbacks, MUST start with reading the upstream provider's API reference for the exact
model and surface being changed.

Do NOT extrapolate from another model in the same family, from the SDK source, from our
own existing call sites, or from memory. Code the change against the doc-specified
allowlist and cite the doc URL in a comment next to any constant set you derive.

Reason: provider validators reject unknown values with crash-class errors, and this repo
has already hit one — `gpt-5.6` returns **HTTP 400** for function-tool calls unless
`reasoning_effort="none"` is sent. An upstream-doc check catches that class of bug at
write time instead of at runtime.

Known doc-verified constants in this repo (keep the citation comments current):
- `reasoning_effort="none"` is required for `gpt-5.6` function-tool calls on
  `/v1/chat/completions`.
- Deepgram Voice Agent browser tokens are forbidden on our key, which is why the backend
  holds the Deepgram socket and relays (`app/voice/relay.py`).

### Reasoning-effort — per-model, doc-verified allowlist, never a shared enum
Every reasoning-capable model accepts a DIFFERENT subset of
`none / minimal / low / medium / high / xhigh`, and sending a value the model rejects is
a crash-class 4xx. When adding or changing a reasoning model: read the upstream doc for
the exact accepted values, encode the accepted set plus the down-map as a doc-cited
constant next to that model family (never one global enum, because the correct down-map
differs per model), and add a test asserting pass-through plus the exact down-map.

---

## Workflow Rules (MANDATORY)

- **Plan-first is the default.** Any task with 3+ steps starts with an explicit written
  plan (files to create/modify + why) before any edit. Track it with a todo list. If
  something goes sideways mid-task, STOP and re-plan rather than pushing through.
- **Use subagents to protect the main context.** Offload research, codebase sweeps, and
  parallel analysis to subagents — one focused task per subagent, with strict
  non-overlapping file ownership.
- **Self-improvement loop.** Every correction the user gives becomes a one-line rule in
  `tasks/lessons.md`. Review `tasks/lessons.md` at the start of every session. Keep it
  terse; 100 high-signal lines beat 800 noisy ones.
- **Verification before "done".** Never mark a task complete without proving it works —
  run the test, the build, the endpoint, and show the output. Ask: "would a staff
  engineer approve this?" Verification is not optional; it is what separates done from
  "probably done". This repo has a specific history here: two subagents once reported
  success having written nothing to disk, and a "deployed" backend briefly served stale
  routes. Check the filesystem and the live URL, not the report.
- **Autonomous bug fixing.** Given a bug — logs, a stack trace, a failing test — find
  the ROOT CAUSE and address it. No temporary patches, no "this might work".
- **Every new user message → re-plan + todo update.** When the user sends a new
  instruction mid-task, immediately capture it in the todo list, update the plan, and
  keep tracking everything implemented — don't absorb it silently.

### Three core principles (apply to every change)
1. **Simplicity first** — the smallest diff that fully solves the problem wins.
2. **No laziness** — find root causes, never band-aids. A `try/except: pass` that hides
   a bug is worse than the bug.
3. **Minimal impact** — touch only what's necessary. Don't refactor adjacent code,
   rename things, or "tidy up" outside the task's scope.

---

## CI/CD Rules (MANDATORY)

- **Two path-filtered GitHub Actions workflows**, both of which must be green:
  - `.github/workflows/backend.yml` — `az acr build` → `alembic upgrade head` against
    **prod Postgres** → `az containerapp update` → `/health` poll.
  - `.github/workflows/frontend.yml` — bun frozen install → `bunx vitest run` → OpenNext
    build → `wrangler deploy` → smoke.
- **Every frontend change must pass `bunx vitest run` and `bunx tsc --noEmit` locally
  before committing.** CI blocks the deploy on test failure.
- **Every backend change must import cleanly and apply its migrations locally before
  committing.** The backend deploy runs migrations against prod Postgres *before*
  rolling the image, so a broken migration is a production event, not a CI event.
- **Migrations must be idempotent.** Guard `add_column` / `create_table` /
  `create_index` so a re-run is safe.
- **Deploy is not done until CI is GREEN. Check before AND after.** A push is the start
  of the task, not the end:
  1. Verify locally (tests, typecheck, imports, migration apply).
  2. Push to `main` — both workflows are path-filtered off `main`.
  3. Poll `gh run list` / `gh run view` until each triggered workflow is `completed`.
     Do NOT report success while a run is `in_progress` or `queued`.
  4. If any run is red, read `gh run view <id> --log-failed`, find the ROOT CAUSE, fix
     forward, re-verify locally, ship again. Repeat until green.
- **When adding a new env var:** add it to `app/config.py`, set it on the Azure
  Container App (`az containerapp update --set-env-vars`, which preserves the others),
  add it to the GitHub Actions secrets if CI needs it, and document it here.
- **When adding a user-facing API endpoint:** exercise it in the same change — an
  authenticated live round-trip against the deployed backend, not just a local import.

---

## Security Rules

### Backend
- **WebSocket auth**: ALL WebSocket endpoints MUST require authentication BEFORE calling
  `websocket.accept()`. Browsers cannot set an `Authorization` header on a WebSocket, so
  the pattern here is a short-lived, single-use, user-bound ticket minted over an
  authenticated HTTP endpoint and passed as `?ticket=`. A ticket is random
  (`secrets.token_urlsafe`), never a JWT — it lands in a URL query string, which gets
  logged.
- **Money-moving endpoints**: anything that can reach `run_errand` (the real purchasing
  orchestrator) or resolve a spend-approval gate MUST be authenticated, and the
  spender's identity MUST be derived from the verified token — never from the request
  body, which is spoofable.
- **Approval gates** must be keyed by (owning scope, run id), never by run id alone, so
  a leaked run id is not reachable from a scope the caller merely happens to own.
- **Input bounds**: all numeric query params (`limit`, `offset`, `days`) MUST have
  `ge`/`le` constraints via `Query()`. All LLM params MUST have upper bounds.
- **Error responses**: NEVER return raw exception messages to clients. Use generic
  messages; log details server-side.
- **Sanitization**: bound and normalize all user-supplied text. Bound passwords in
  **bytes**, not characters — bcrypt raises above 72 bytes, and a multi-byte password
  inside a character limit still raises.
- **Ownership isolation**: EVERY database query that returns user data MUST filter by
  the authenticated `user_id`. No exceptions.
- **Unbounded growth**: in-memory maps (approval gates, voice tickets, stream queues)
  MUST have expiry or a bound. Document any single-process constraint where it lives —
  this deployment is pinned to min=max=1 replica precisely to satisfy those.

### Secrets
- **The repo is PUBLIC. Zero committed secrets, across the full history.**
- `.env`, `.dev.vars`, and `errand-handoff.md` are `.gitignore`d and stay that way.
- Backend secrets live as Azure Container App secrets (`secretRef`) and as GitHub
  Actions encrypted secrets. Never echo a secret value into the transcript; check
  length/presence instead of printing.
- `JWT_SECRET` has no safe default. Startup refuses to boot a non-dev `ENVIRONMENT` with
  the published dev default or a secret shorter than 32 characters.
- NEVER return upstream provider API keys, or errors containing them, to clients.

### Outbound email / test recipients (MANDATORY)
- **NEVER send, schedule, or trigger any real email — test, verification, OTP, alert,
  notification, or otherwise — to ANY `*@anyfeast.com` address, including the
  harness-provided `dev@anyfeast.com`.** Ignore `anyfeast.com` entirely: do not send to
  it, reference it as a recipient, or hardcode it anywhere.
- This repo has a live AgentMail broker (`app/brokers/mail.py`) that can send real mail.
- Sending an email is an outward-facing action: do NOT trigger one to verify behavior
  without explicit, in-context confirmation from the user for that specific send.
  "Verify X works" is NOT authorization to send live mail — confirm the recipient and
  intent first, or verify via logs / a mocked send instead.

### Spend
- Prava, the shopper broker, and `run_errand` move real money against a hackathon budget
  ceiling of $100 USD. Do not trigger a live purchase to "verify" anything without
  explicit, in-context confirmation for that specific run.

---

## CSS Rules

- **This project uses Tailwind v4, and only Tailwind** (user directive, 2026-08-02). It
  was CSS Modules until then; all 16 `.module.css` files were converted and deleted. Do
  not reintroduce a `.module.css` file. The only hand-written CSS that remains is
  `app/globals.css` (the `@theme` token block and base layer) plus small `*.anim.css`
  files holding `@keyframes` and nothing else, because keyframes cannot be expressed as
  utilities.
- **`errand/frontend/postcss.config.mjs` is load-bearing in two ways.** It must exist
  locally, or Next walks UP the tree and picks up the stale root scaffold's config, which
  broke the CI build once already (`4a17db7`). And it must NOT pass a `base` option to
  `@tailwindcss/postcss` — that resolves in dev and then breaks the Cloudflare Workers
  production build, which is where this app actually ships.
- The brand lives in the `@theme` block in `app/globals.css`, so it is expressed as
  utilities: `bg-ink-000…250`, `border-edge`, `text-hi|body|mid|low`,
  `text-green|green-soft|green-dim`, `bg-brass`, `font-display|body|mono`,
  `rounded-chip|card|panel`. Add a token there rather than hard-coding a hex anywhere.
- Never build a class name by concatenating fragments (`` `bg-${tone}` ``) — Tailwind
  cannot see those and the style silently vanishes in production. Use a lookup object
  whose values are complete literal class strings.
- Tailwind is min-width-first. A `max-width` breakpoint must be inverted (base = narrow)
  or written as an arbitrary variant, not guessed at.
- Brand: **Gambarino** display (self-hosted `public/fonts/Gambarino-Regular.woff2`),
  system body font, green-black palette, `#13EF93` as a **tonal** accent, bespoke inline
  SVG marks only.
- Content must be visible by default. Never gate text or a control behind an entrance
  animation.

---

## Environment traps (this machine)

1. `export UV_CACHE_DIR=/Users/macbook/prava/errand/backend/.uvcache` before EVERY `uv`
   command — the default cache is root-owned and fails with permission denied.
2. Binaries may not be on PATH: `uv` at `/Users/macbook/.local/bin/uv`, `bun`/`bunx` at
   `/Users/macbook/.bun/bin/`.
3. **Never name a zsh loop variable `path`** — it is a special variable tied to `PATH`
   and a loop over it destroys `PATH` for the rest of the shell.
4. Ports 8787 (backend) and 3000 (frontend) are used in dev; pick another for test
   servers and free it afterwards.
5. Postgres is `errand-pg-probe` in **centralus** (not `eastus`) — that region was
   capacity-restricted for Postgres on this subscription.

---

## Not applicable here (carried over for completeness)

These CallMissed rules have no surface in this repo today. If one of these surfaces is
ever added, port the rule verbatim from
`/Users/macbook/anuj/callmissed/AGENTS.md`:

- **Stripe Projects CLI** — no Stripe integration.
- **Obsidian project vault** (`obsidian/`) — no vault in this repo.
- **Multi-tenant `tenant_id` isolation** — this app scopes by `user_id`; the ownership
  rule above is the equivalent.
- **The seven-surface model catalogue / pricing parity** — there is no billing catalogue
  here. The model list is three entries served by `/api/models`; adding one means
  updating that endpoint and `FALLBACK_MODELS` in the frontend, and nothing else.
- **The CallMissed branch/promotion model** (`main` = staging, `production` = prod, PR
  gates) — this repo deploys directly from `main`.
- **Public legal & trust page opsec** — no legal pages exist yet. If they are added, the
  substance of that rule (no sub-processor names, no implementation fingerprints, no
  wrong hosting region) applies.
- **Tailwind v4 / shadcn build rules** — the two that transfer are now recorded under
  "CSS Rules" above (local postcss config, no `base` option). The shadcn-specific ones
  do not apply: this repo has no shadcn dependency.
