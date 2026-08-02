# Errand — UI Brief (single source of truth for the UI build)

## What we're building
A single Next.js app for the "Errand" agent: a voice-first + chat agent that
shops a real merchant and pays via Prava, with a human approving every spend.
Two personas share one engine: **business** (procurement) and **personal**
(errands). A profile toggle switches context; the UI is otherwise identical.

## UI FOUNDATION — Deepgram UI kit (locked)
Use the official Deepgram kit, NOT shadcn/21st.dev as a component source:
- `@deepgram/ui` (v0.1.4) — pre-built styled components: `AgentProvider`,
  `AgentConversation`, `AgentStartButton`, `AgentTextInput`, and the
  **audio-reactive orb** (example 15-react-ui-orb). Fully themeable.
- `@deepgram/react` — provider + hooks (`useAgentState`, `useAgentConversation`).
- Reference examples in `reference-repos/deepgram-agent/examples/`
  (13-standalone, 14-voice-button, 15-orb). Lift these patterns directly.
The orb is the signature artifact and it's audio-reactive out of the box.

## Voice + tools architecture (the key insight)
The Deepgram `AgentProvider` config carries the whole voice loop AND tool-calling:
```
agent: {
  listen: { provider: { type:"deepgram", model:"nova-3" } },   // STT
  think:  { provider: { type:"open_ai", model:"gpt-4o-mini" }, // LLM (OpenAI track)
            prompt: "<errand agent system prompt>",
            tools: [ getContext, buildCart, createSession,
                     approveSpend, waitForConfirmation ] },     // ← our brokers
  speak:  { provider: { type:"deepgram", model:"aura-2-thalia-en" } }, // TTS
  greeting: "..."
}
```
So the orchestrator brokers are exposed as the agent's `think.tools`. The tool
handlers call our server (Prava/Senso/Shopper/Mail brokers). `approveSpend` is the
human-in-the-loop gate: it renders the cart + Prava iframe (passkey) and blocks
until approved.

## Secrets
`tokenFactory` pattern: browser fetches a short-lived JWT from `/api/token`; the
real Deepgram key stays server-side. Prava publishable key is client-safe (iframe
SDK). Prava secret, Senso, AgentMail keys are server-only.

## Screens / states (one page, staged)
1. Idle/welcome — brand mark, the orb (signature), profile toggle (Business |
   Personal), text composer as secondary input. Voice is the hero.
2. Listening/thinking — orb reacts to live audio (kit handles this).
3. Plan — grounded plan with Senso **citations** as small source chips.
4. Cart — cart card: items, qty, price, total vs budget.
5. Approve spend — inline card → mount Prava iframe (passkey). Emotional peak.
6. Working — checkout progressing (agent typing into merchant); compact status.
7. Done — order confirmation + audit log (context→cart→approve→pay→email).

## Data it renders (shapes in src/lib/contracts)
- PurchaseContext { approvedMerchants, budgetCents, rules[], citations[] }
- CartResult { items[], totalCents, checkout }
- CreateSessionResult { sessionId, iframeUrl }  ← mount iframe for passkey
- AuditEvent[] { at, step, detail }  ← audit log
- OrderResult { orderId } + confirmation email (MailBroker)

## Design law — NON-NEGOTIABLE (from AGENTS.md anti-slop law)
Read the whole anti-slop law and do a point-by-point pass before shipping.
The Deepgram kit uses shadcn tokens internally — that's fine, but we still
art-direct HARD on top for one cohesive brand; do not ship the default theme.
Hard "do NOT" list:
- No blue→purple gradients, no cool blue-charcoal dark default, no candy pastel
  bg, no drifting gradient blobs, no radial glow halo behind the orb.
- No glowy pill buttons, no filled+outline button pair, no default hero stack,
  no icon-in-a-tile, no sun/moon toggle, no hover-lift, no growing-underline.
- Signature face: self-host ONE distinctive display face (Fontshare ok) + neutral
  body (system-ui acceptable). No Inter/Space Grotesk/Sora/Geist as the brand.
- One cohesive palette chosen for THIS brand (not stock gray, not cream). Note
  the kit's accent is #13EF93 (Deepgram green) — decide whether to keep or
  retheme; if kept, build the whole palette deliberately around it.
- Center what must be centered; verify optically. Clear any cut/clip.
- Content visible by default — never hide behind an entrance animation.
- Professional WITH a heartbeat: the orb is the authored motion moment; states
  clean and aligned; real audit data. Not empty minimalism.

## Tech constraints
- Next.js (App Router) + TypeScript. Engine already exists in `src/lib/*`
  (contracts, brokers, orchestrator). Tool handlers call the brokers.
- Deepgram kit is Tailwind v4 based — the app uses Tailwind v4 for the kit to
  theme correctly.
