# Errand — Chat UI Rebuild Brief (ChatGPT-style with animated tool calls)

Goal: turn Errand's staged-panel UI into a **conversational chat thread** (ChatGPT/
Claude style) where the agent's work appears as **animated tool-call cards** inline
in the conversation — "visual MCP" style. Keep our brand + backend contract intact.

## Reference to borrow FROM (patterns, not pixels)
- `reference-repos/better-chatbot/src/components/message-parts.tsx` — how tool
  calls render inline as collapsible cards with `framer-motion`
  (`AnimatePresence`, `motion.div`), a running→result state, expand/collapse.
- `reference-repos/better-chatbot/src/components/tool-invocation/*` — per-tool
  visual renderers (web-search, sequential-thinking, charts, interactive-table).
  Borrow the STRUCTURE (header with icon + shimmer while running, body reveals
  result, collapsible) — NOT their Tailwind/shadcn styling.
- `TextShimmer` shimmer-while-thinking effect and word-by-word fade-in.
We use `motion` (installed). Do NOT add shadcn, Tailwind, or their component kit.

## The new layout (one page)
A chat thread, not stacked stages:
- **Center column chat thread** (max ~760px, centered) that scrolls; newest at
  bottom; auto-stick-to-bottom while streaming.
- **Composer pinned at the bottom** (like ChatGPT): text input + send, the
  **VoiceOrb** as the mic button (tap to talk, it already reacts to real audio),
  the **ModelSelector** (Sol/Terra/Luna, custom SVGs) and the **ProfileToggle**
  (Business|Personal) live in/near the composer.
- **Empty state**: brand mark + a short line + a few example prompts as chips
  ("Restock the office pantry under $200"). Clicking a chip fills the composer.

## Messages & tool cards (the core)
Each run becomes a conversation turn:
1. **User bubble**: the intent (typed or spoken).
2. **Assistant turn**: a sequence of **tool-call cards**, one per backend SSE
   step, animating in as they arrive (`AnimatePresence` enter). Map events →
   cards:
   - `context.loaded` → "Consulted policy (Senso)" card: budget + rules + citation
     chips. Icon = document/shield. Running state shimmer → resolved shows data.
   - `cart.built` → "Built cart" card: line items, qty, prices, total vs budget
     meter. (Reuse CartPanel content inside a tool card.)
   - `payment.session` → "Opened secure payment (Prava)" card.
   - `approval.request` → **inline approval tool card** (the key interactive one):
     shows cart + total, Approve (primary) + "Not now" (quiet). Approve mounts the
     Prava iframe (passkey) then POSTs approve; decline POSTs {approved:false}.
   - `payment.credential` → "Issued one-time card" card (last4).
   - `checkout.completed` → "Placed order" card (order id, confirmation text).
   - `payment.reported` → subtle inline status (can be a small line, not a full card).
   - `mail.confirmation` → "Confirmation email received" card (from agent inbox).
   - `run.done` → assistant closing bubble ("Done — order ORD-… for $X.").
   - New events handled gracefully: `approval.timeout`, `payment.declined`,
     `run.aborted`, `payment.report_failed`, `run.error` → clear inline states,
     never crash; unknown events still render as a generic card.
3. While a step is in-flight (between events), show a **shimmer "working…" tool
   card** with the current phase label so the thread always feels alive.

## Tool-card anatomy (consistent, animated)
- Header row: small tool icon (bespoke stroke, our style) + tool title + a status
  chip (running = shimmer text; done = check; error = alert). Click header to
  expand/collapse the body.
- Body: the tool's data (reveals with a height/opacity motion). Collapsible.
- Enter animation: fade + slight rise (y: 6 → 0), fast (150–200ms), eased. NEVER
  hide content behind a reveal that could fail — content is present; motion only
  decorates arrival. Respect prefers-reduced-motion.

## Voice + streaming (unchanged contracts)
- Keep SSE consumer (`lib/stream.ts`, `lib/useErrandRun.ts`) — it already handles
  reconnect + all events. The chat thread renders from the same event stream; the
  audit log becomes the natural chat transcript (you can keep a collapsible "raw
  audit" for judges, but the thread IS the primary view now).
- VoiceOrb stays the signature; in chat it lives as the animated mic control and
  still reacts to real mic audio. Keep the phase→motion mapping.

## Brand / anti-slop (unchanged, strict)
Read `/Users/macbook/.config/opencode/AGENTS.md`. Keep: Gambarino display +
system body, green-black palette, #13EF93 accent (tonal), brass secondary, custom
SVG marks, ONE orb. NO: blue/purple, glowy pills, filled+outline pair, icon-in-
tile, hover-lift, growing underline, gradient headline, drifting blobs, radial
halo. Tool-card icons must be our own stroke language, not an icon pack dump.
Content visible by default. Do a point-by-point anti-slop pass before done.

## Verify
- `cd frontend && bunx next build` passes.
- Run against backend (mock payment) and confirm the chat thread renders tool
  cards animating in across a full run, approval works inline, decline works,
  reconnect state shows, done bubble appears.
