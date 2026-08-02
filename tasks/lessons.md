# Lessons

One line per correction. Terse. Review at the start of every session.
Newest at the bottom.

- Model selector and Business/Personal toggle stay at the TOP of the chat UI — explicit user request.
- Do not reproduce a real company's site, copy, or assets; take design language only and build something original.
- No fake logos, invented customers, or made-up stats in any marketing copy.
- Repo is public: zero committed secrets, across the whole history. `errand-handoff.md` is gitignored.
- Speed is the priority — prefer acting over asking; parallelise independent work across subagents with strict non-overlapping file ownership.
- A subagent reporting success is not evidence; check the filesystem and the live URL. Two subagents once reported success having written nothing.
- A green deploy is not evidence the new code is serving; poll the live endpoint. A "deployed" backend once briefly served stale routes.
- `export UV_CACHE_DIR=/Users/macbook/prava/errand/backend/.uvcache` before every `uv` command — default cache is root-owned.
- Never name a zsh loop variable `path`; it destroys `PATH` for the rest of the shell.
- `gpt-5.6` needs `reasoning_effort="none"` for function tools on `/v1/chat/completions`, else HTTP 400.
- `az containerapp update --set-env-vars` preserves the other env vars; `--replace-env-vars` does not. Verify the var landed on the ACTIVE revision, not just the app.
- Auth register returns the JWT as `token`, not `access_token`.
- `email-validator` rejects reserved TLDs (`.invalid`, `.test`); use `example.com` for throwaway test accounts.
- Bound passwords in BYTES, not characters — bcrypt raises above 72 bytes and a multi-byte password inside a character limit still raises.
- 2026-08-02 (user): read the provider's upstream doc before touching any provider call; never extrapolate from a sibling model, the SDK source, our own call sites, or memory.
- 2026-08-02 (user): every new user message mid-task → re-plan and update the todo list immediately; do not absorb it silently.
- 2026-08-02 (user): never send real email to any `*@anyfeast.com` address, including `dev@anyfeast.com`.
- 2026-08-02 (user): this project is Tailwind v4 only now — no CSS Modules; keep postcss.config.mjs local and never pass it a `base` option (breaks the Cloudflare Workers build).
- 2026-08-02 (user): when told to go faster, fan out subagents with strict non-overlapping file ownership and forbid them from running `next build` concurrently (they collide in .next).
- Prava's card credential appears at status `awaiting_result`, not `completed` — keying on `completed` alone silently times the errand out with no 4xx anywhere.
- Deepgram's BYO think endpoint needs the FULL /v1/chat/completions path and `reasoning_mode` (its name for OpenAI's reasoning_effort).
