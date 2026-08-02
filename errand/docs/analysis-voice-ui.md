# Voice + Chat UI patterns to adopt into Errand

Read-only analysis of five cloned reference repos, measured against Errand's
actual current implementation (Web Speech API + Web Audio orb + hand-parsed SSE),
**not** the older `ui-brief.md` (which assumed the Deepgram WS kit). Focus is on
patterns that measurably improve Errand *without* breaking brand cohesion.

Our current baseline (for reference):
- SSE: `frontend/lib/stream.ts:30-92` — fetch + manual frame parse, AbortController, **no reconnect**.
- Voice: `frontend/lib/useVoice.ts` — AnalyserNode → 5 bands + Web Speech transcript.
- Orb: `frontend/components/VoiceOrb.tsx` — canvas, 5 lobes, directional self-glow, phase tint at `:67-74`.
- Approval: `frontend/components/stages/ApprovalPanel.tsx` + `frontend/lib/useErrandRun.ts:169-266`.
- State machine: `frontend/lib/useErrandRun.ts:24-33` (RunPhase) — SSE frames → phases + audit.

---

## 1. Top 5 recommendations (ranked by value / effort)

### R1 — State-driven orb motion (idle / listening / thinking / working), not just tint. Effort: S
**What it is.** LiveKit maps `AgentState` → animated motion *parameters* (speed,
amplitude, frequency, opacity), not just color. See
`agent-starter-react/hooks/agents-ui/use-agent-audio-visualizer-wave.ts:60-105`:
`listening` = slow breathing + opacity mirror-pulse; `thinking` = 4× speed / ¼
amplitude tight flutter; `speaking` = amplitude driven live by volume. The bar
animator adds a deterministic "sequencer" for non-audio states
(`use-agent-audio-visualizer-bar.ts:4-43`) — a scripted sweep for connecting, a
center pulse for thinking — so the visualizer is *alive even with zero audio*.

**Why it helps Errand.** Our orb only reacts when the mic is live and only
changes tint per phase (`VoiceOrb.tsx:67-74`). During `planning`/`working`
(SSE flowing, mic off) it just idle-breathes — it looks inert exactly when the
agent is busiest. Giving each `RunPhase` its own motion signature makes the one
signature artifact carry state, which is squarely the "professional WITH a
heartbeat" mandate.

**How to adopt.** Stay 100% in our canvas orb. In `VoiceOrb.tsx`, drive the
existing `breathe`/`energy`/`rot`/`wobble` scalars from `phase` when `live` is
false: e.g. `planning`→faster rotation + low-amp flutter, `working`→steady
pulse on `coreR`, `awaiting_approval`→slow hold. Feed `phase` (already a prop)
through a small `phaseMotion(phase)` returning `{speedMul, ampMul, pulse}`. No
new deps, no Motion, no LiveKit — pure additions to the RAF loop.

**Anti-slop risk.** None if kept in-canvas and in the green tonal family. Do
NOT import the LiveKit shader visualizers (see Reject list) — they'd fork the
signature.

---

### R2 — SSE reconnect + resume so a dropped connection doesn't kill a live run. Effort: M
**What it is.** Two complementary patterns:
- Client keepalive/retry discipline — Deepgram's `KeepAliveTimer`
  (`deepgram-agent/packages/sdk/src/connection/keepalive.ts`) and the demo's
  ping loop + `retryOnError` + cache-busted URL
  (`deepgram-ai-agent-demo/app/context/WebSocketContext.tsx:356-367,150,495`).
- Server-side resumable stream — assistant-ui keeps the producer running server
  side keyed by a `streamId` header, and the client reconnects with a GET to
  `/resume/:id` to continue mid-flight with no lost frames
  (`assistant-ui/examples/with-resumable-stream/app/api/chat/route.ts:31,44-51`
  and `.../resume/[streamId]/route.ts:11-27`; store in `lib/resumable-context.ts`).

**Why it helps Errand.** `stream.ts` opens one fetch and, on any network blip
during the *minutes-long* errand run, just fires `onError` and drops to the
error phase (`stream.ts:46-52,84-88`) — the run is lost mid-checkout. This is
the highest-risk gap for a payment flow. There's no retry and no way to
re-attach to an in-flight run.

**How to adopt.**
- *Now (S, client-only):* wrap the reader loop in `stream.ts` with bounded
  exponential-backoff reconnect (e.g. 3 tries, 0.5s→4s) on non-abort errors,
  and only surface `onError` after retries exhaust. Track a "reconnecting"
  substate so the UI can show it instead of a hard failure.
- *Later (M, needs backend):* have the backend accept a `Last-Event-ID`
  (native SSE header) or a `run_id` on reconnect and replay/continue from the
  last audit sequence. Our `useErrandRun.ts` already assigns monotonic
  `auditSeq` (`:78,87`) — dedupe replayed frames by it. The assistant-ui
  route pair is the exact template.

**Anti-slop risk.** None (pure infra). UX note: a small "reconnecting…" state
on the orb (ties into R1) is enough — don't add a spinner modal.

---

### R3 — Barge-in: interrupt TTS the instant the user speaks. Effort: S (conditional on TTS)
**What it is.** `AgentPlayer.interrupt()` flushes all queued PCM on
`UserStartedSpeaking` (`deepgram-agent/packages/sdk/src/audio/player.ts:103-110`);
the demo does the same via `clearScheduledAudio()` stopping every scheduled
`AudioBufferSource` (`WebSocketContext.tsx:168-172,336-353`). The scheduling
model itself — `nextStartTime` gap-free queueing (`player.ts:86-88`) — is the
clean way to play streamed TTS.

**Why it helps Errand.** Only relevant if/when Errand speaks (TTS). Today we're
STT-only, so this is *conditional*. If TTS lands, barge-in is the single
biggest voice-UX quality signal: talking over the agent must instantly cut it.

**How to adopt.** If we add TTS, lift `AgentPlayer` almost verbatim (it's
dependency-free, 150 lines) and call `.interrupt()` from `useVoice` the moment
Web Speech emits any interim result (`useVoice.ts:189-199` — we already have
the interim signal there). Keep our mic AnalyserNode path untouched.

**Anti-slop risk.** None (audio logic, no UI). Skip entirely until TTS is real.

---

### R4 — Dual input/output volume feeding one orb. Effort: S (conditional on TTS)
**What it is.** The Deepgram Orb takes BOTH `getInputVolume` and
`getOutputVolume` and switches which drives the animation by mode
(`deepgram-agent/examples/15-react-ui-orb/index.html:44-56,85-93`). Both use the
same RMS-on-time-domain-data formula (`player.ts:41-51`, `microphone.ts:43-53`),
which is cleaner than our frequency-average level (`useVoice.ts:113-120`).

**Why it helps Errand.** When the agent speaks, the orb should pulse to the
*agent's* voice, not sit idle or react to room noise. One orb, two sources,
switched by phase — the signature stays singular.

**How to adopt.** When TTS exists, add an output AnalyserNode (as in `player.ts`)
and in `VoiceOrb` select input-vs-output level by `phase`
(`working`/speaking → output). Optionally swap our level math for the RMS
time-domain formula — it's more perceptually stable. Small, in-canvas.

**Anti-slop risk.** None. Conditional on TTS like R3.

---

### R5 — Typed args/result tool-card contract for the approval + future tool UIs. Effort: M
**What it is.** assistant-ui renders a tool call as a component that receives
`{ args, result, addResult }` and swaps presentation by whether `result` exists:
interactive form while pending, confirmation once resolved
(`assistant-ui/examples/with-generative-ui/components/contact-form-tool-ui.tsx:18-40,79-86`).
The `addResult(...)` call is the human-in-the-loop resolution.

**Why it helps Errand.** Our `ApprovalPanel` hard-codes one flow and the
"resolve the gate" action lives in a separate POST in `useErrandRun.approve`
(`useErrandRun.ts:245-266`). Adopting the *shape* — a card keyed on a tool/step
that renders pending→resolved and calls one resolver — future-proofs us for the
other brokers (`buildCart`, `getContext`) becoming inline approvable/editable
cards, and makes the approval card's two states explicit. Note the "Not now"
button in `ApprovalPanel.tsx:119-121` is currently inert — this pattern gives it
a real resolver (`addResult({approved:false})`).

**How to adopt.** Do NOT install `@assistant-ui/react` (see Reject). Borrow the
*contract* only: define `type ToolCardProps<A,R> = { args:A; result:R|null;
resolve:(r:R)=>void }` in `lib/types.ts`; refactor `ApprovalPanel` to that shape
with `resolve` wired to the existing approve POST; wire the "Not now" button to
`resolve({approved:false})`. Keep all our CSS-module styling.

**Anti-slop risk.** Medium if misread as "adopt the library" — the library is
Tailwind/shadcn and would drag a mismatched look. Copy the *interface pattern*,
not the components.

---

## 2. REJECT list (tempting, but hurts cohesion or duplicates what we have)

- **LiveKit shader visualizers (aura / wave / bar / radial / grid).**
  `agent-starter-react/components/agents-ui/agent-audio-visualizer-*.tsx`. The
  aura is a WebGL shader defaulting to `#1FD5F9` cyan with a rainbow `colorShift`
  (`agent-audio-visualizer-aura.tsx:22,144-160`) — a second, competing signature
  artifact. We already have a bespoke canvas orb; a shader aura beside it is two
  heroes. **Take the state→motion mapping (R1), leave the shaders.**
- **`@assistant-ui/react` runtime + component kit.** Powerful, but it's a
  Tailwind/shadcn/Radix world (see its AGENTS.md) with its own theming. Adopting
  it means shipping the default kit look and a second styling system next to our
  CSS modules — direct anti-slop violation. **Take the tool-card contract (R5).**
- **The whole Deepgram WebSocket agent stack** (`WebSocketContext.tsx`,
  `AgentProvider`). It's built for browser→Deepgram direct WS, which our key
  can't mint tokens for (`useVoice.ts:3-6`). Grafting it in now = dead
  architecture. **Take keepalive + barge-in + resumable (R2/R3) as isolated
  utilities; skip the provider.** Also its mic path uses the deprecated
  `ScriptProcessorNode` (`WebSocketContext.tsx:405`) — the SDK's AudioWorklet
  (`microphone.ts:87-104`) is the correct version if we ever relay to Deepgram.
- **shadcn-chatbot-kit chat components** (`message-list`, `typing-indicator`,
  `prompt-suggestions`, `interrupt-prompt`). Generic shadcn chat UI; we render
  staged panels, not a chat log, and these would import the stock look. The one
  idea worth *stealing conceptually* is the interrupt affordance copy (below) —
  not the component.
- **`streamdown` / `Streamdown` markdown bubbles**
  (`agent-chat-transcript.tsx:4,136`). We don't render freeform markdown chat;
  our output is structured panels. Adding a markdown renderer is scope we don't need.
- **Groq/Whisper transcribe route** (`shadcn-chatbot-kit/.../transcribe/route.ts`).
  A batch file-upload STT — the opposite of our streaming interim/final Web
  Speech path. No benefit.
- **`react-use-websocket` dependency** (`WebSocketContext.tsx:12`). We're SSE,
  not WS; the reconnect logic we need (R2) is ~20 lines in `stream.ts`.

---

## 3. Copy-paste-able small utilities (with source path)

**a) RMS volume from time-domain data** — more stable than frequency-average.
Source: `deepgram-agent/packages/sdk/src/audio/player.ts:41-51` &
`microphone.ts:43-53`. Drop-in replacement for the level calc in
`useVoice.ts:113-120`:
```ts
// analyser.getByteTimeDomainData(data) into a Uint8Array first
let sum = 0;
for (let i = 0; i < data.length; i++) { const v = (data[i]-128)/128; sum += v*v; }
const level = Math.min(1, Math.sqrt(sum / data.length) * 4);
```

**b) KeepAlive/interval timer** — trivial, dependency-free, reusable for any
ping loop. Source: `deepgram-agent/packages/sdk/src/connection/keepalive.ts`
(whole file, ~22 lines).

**c) JWT-exp-aware caching token factory** — if we ever mint short-lived
browser tokens (Prava publishable key rotation, future Deepgram relay). Reads
`exp` off the JWT, refreshes 5s early, `invalidate()` before reconnect. Source:
`deepgram-agent/packages/sdk/src/token/factory.ts:15-70` (whole `CachingTokenFactory`).

**d) Gap-free streamed-PCM scheduler** — for TTS playback if added.
`nextStartTime`-based queueing so chunks play back-to-back with no clicks.
Source: `deepgram-agent/packages/sdk/src/audio/player.ts:64-97`.

**e) "Press Enter again to interrupt" affordance (copy/idea only).** The barge-in
UX cue. Source: `shadcn-chatbot-kit/.../ui/interrupt-prompt.tsx`. Reimplement in
our own styling if we add TTS — it's the right micro-affordance, wrong look.

**f) Resumable-stream server context (template).** For R2's server half.
Source: `assistant-ui/examples/with-resumable-stream/lib/resumable-context.ts`
(in-memory + Redis store) and the route pair
`app/api/chat/route.ts` + `app/api/chat/resume/[streamId]/route.ts`.

---

### Sequencing
R1 (orb state motion) and R2-client (reconnect) are the immediate, dependency-free,
high-value wins — do those first. R3/R4 are gated on TTS existing. R5 and R2-server
are the medium-effort structural improvements to schedule when touching the
approval flow / backend next.
