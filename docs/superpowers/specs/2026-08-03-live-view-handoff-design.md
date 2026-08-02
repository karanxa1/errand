# Live-view browser handoff — design

Date: 2026-08-03
Status: approved (user: "let user do payment through the controllable browser,
check the Cloudflare docs and implement everything"), building.

## Goal

The agent shops autonomously in a REAL Cloudflare browser (navigate, search,
add to cart, attempt registration), then hands off a **live, interactive** view
of that same browser to the human, who logs in / pays themselves. The agent
never enters the card. After the human marks done, the agent reads the order
confirmation. This is the "authenticated real merchant" path the Prava wallet
cannot do (wallet = guest checkout only).

## Doc-verified facts (Cloudflare Browser Run; researched 2026-08-03)

- `Cloudflare.getLiveView({ mode, targetId?, expiresInMs? })` → `{ devtoolsFrontendUrl }`.
  `mode: "tab"` is INTERACTIVE and is the one that supports handoff.
  `expiresInMs` default 5 min, max 1 h — the URL's validity, refreshable by
  re-issuing the command.
- Structured handoff: `Cloudflare.handoff({ instructions, targetId?, timeout? })`
  → `{ handoffId }`; the `Cloudflare.handoffComplete` event fires with
  `{ success, reason? }` when the human clicks **Done**/**Failed** in the live
  view (or the handoff times out; `timeout` max 30 min, undefined = no timeout).
- Python Playwright sends these via `cdp = await context.new_cdp_session(page)`
  then `await cdp.send("Cloudflare.getLiveView", {...})`. No `.once()` in Python
  — subscribe with `cdp.on("Cloudflare.handoffComplete", …)` and resolve a future.
- Session lifecycle: `keep_alive` is an INACTIVITY timeout, default 60s, **max
  600000ms (10 min)**. `browser.disconnect()` (not `.close()`) keeps it alive to
  reconnect. NO fixed max lifetime while active.
- `live.browser.run` sends `content-security-policy: frame-ancestors *`
  (verified by curl) → it **can be embedded in an iframe**.
- Warning (quoted): "Browser Run requests are always identified as bot traffic.
  Even with a human controlling the session, some third-party services may still
  block the request." → a hostile merchant may still refuse. Not promised.

## The critical risk and how we handle it

The session idles out at ≤10 min, and a human paying may sit still longer than
that (or the session may idle while they type). Mitigations, all doc-grounded:

1. Create/keep the session with `keep_alive=600000` (already the template value).
2. During the human wait, send a lightweight CDP command on a timer (a
   keep-alive PING, e.g. `Runtime.evaluate "1"` or `Cloudflare.getHandoffState`)
   so the inactivity timer never fires while a handoff is open.
3. Bound the whole handoff to a wall-clock budget (default ~8 min, under the
   ceiling) and, on timeout, end cleanly with an honest "the checkout window
   closed, nothing was completed" — never a hung run.
4. The browser handle lives in this process's heap (single-replica constraint,
   same as the existing run). A restart mid-handoff loses it; the run ends with
   that honest error rather than pretending. Documented, not hidden.

## Architecture (non-overlapping backend / frontend split)

Wire contract (the seam both sides build to):
- server→client SSE frame `browser.liveview` `{ run_id, url }` — the interactive
  live-view URL. Reducer stores it as the latest `liveView` (bounded, like
  `browserFrame`).
- client→server the human's "done"/"cancel" — reuses the EXISTING approval
  rendezvous (`POST /api/conversations/{id}/approve`, the DB `Approval` row),
  because it is already the durable, ownership-scoped (owner,run_id) hand-off.
  A handoff "approve" = "I finished in the browser"; "decline" = "cancel".

### Backend bundle
- `app/brokers/shopper.py`: a `shop_live_handoff(merchant_url, intent, context,
  decide, on_frame, on_live_view, wait_for_human)` that opens ONE session, runs
  the agentic loop to fill the cart, calls `Cloudflare.getLiveView(mode="tab")`,
  emits the URL via `on_live_view`, opens `Cloudflare.handoff`, pings keep-alive
  while awaiting `handoffComplete` OR the injected `wait_for_human` signal
  (whichever first), then reads the confirmation off the SAME page and returns an
  OrderResult. Local (non-Cloudflare) targets can't Live View → clean refusal.
- `app/orchestrator/run_errand.py`: not on the critical path for v1 — the live
  tool drives the shopper directly from chat (simpler, and keeps the Prava
  money-path invariants in run_errand untouched). Revisit if we want the policy
  ladder in front of it.
- `app/routers/chat.py`: register `shop_live` tool; `do_shop_live` handler that
  builds a Cloudflare shopper, streams `browser.frame`/`browser.liveview`, and
  gates on the existing approval rendezvous for the human-done signal; dispatch
  branch. `reasoning_effort="none"` on any model call.
- `app/config.py`: `use_live_handoff: bool = False` + a readiness property
  (needs Cloudflare creds).
- Tests: shopper handoff with a fake CDP/page; the tool's frame flow.

### Frontend bundle (subagent, zero shared files with backend)
- `lib/errandReducer.ts`: `liveView: {url} | null` state; `browser.liveview`
  handler (latest-only, ignore empty); clear on `run.started`.
- `components/chat/Thread.tsx`: `LiveViewCard` — an interactive `<iframe>` to the
  live-view URL with the ApprovalPanel discipline (sandbox, stall detection,
  "open in new tab" fallback), plus a "Done paying" / "Cancel" control wired to
  the approval resolver.
- `lib/types.ts`: the new frame/payload type.
- `lib/errandReducer.liveView.test.ts`: pins the reducer contract.

## Invariants preserved
The Prava money-path in run_errand is untouched. The live handoff is a distinct,
opt-in tool. The human still performs the actual payment (now literally, in the
browser), so "nothing charged without the human" holds even more strongly — the
agent has no card on this path at all.

## Out of scope (v1)
- Voice-driven live handoff (the voice relay doesn't forward on_frame yet).
- Durable resume across a backend restart (single-replica; ends honestly).
- Any promise that a specific hostile merchant (SHEIN/Amazon) won't bot-block.
