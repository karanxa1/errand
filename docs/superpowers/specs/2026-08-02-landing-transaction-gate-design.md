# Errand Landing Transaction Gate + SSR Seed — Design

**Date:** 2026-08-02
**Status:** Approved for specification review

## Goal

Make the landing page the strongest visual artifact in the hackathon submission
without misrepresenting the product. The page communicates one idea in the
first viewport: Errand can move a purchase through policy, cart, and payment
preparation, but the transaction physically stops at a human approval gate.

In the same delivery, seed `/c/[id]` with server-rendered conversation history
using a first-party httpOnly cookie mirror. This SSR addition must not alter the
working browser-to-backend SSE, voice, or mutation paths.

## Success Criteria

1. The first viewport is recognizably Errand rather than a generic SaaS page.
2. The signature transaction path explains the product without requiring a
   paragraph of copy.
3. Approve, decline, replay, navigation, and authentication controls work when
   clicked in a real browser.
4. Content remains visible if JavaScript animation never runs.
5. Desktop and mobile preserve complete text, controls, and transaction data
   without clipping or accidental horizontal overflow.
6. A hard load of an authenticated `/c/[id]` can include the latest conversation
   window in server HTML after the first-party session mirror exists.
7. Existing direct SSE, voice WebSocket, voice-ticket, and Bearer-token requests
   retain their current routes and behavior.

## Audience and Truthfulness

The primary audience is a hackathon judge assessing product clarity, Prava's role,
technical execution, and visual craft. The page uses plausible demonstration data
and labels it as a demonstration. It must not invent customers, logos, completed
orders, performance claims, or an end-to-end sandbox purchase that has not yet
been verified.

## Visual World

The existing warm green-black palette, Gambarino display face, system body face,
bare Errand mark, and tonal green/brass accents remain the brand foundation. The
redesign adds authored transaction geometry and motion rather than changing the
brand into a marketing skin.

The page is treated as one continuous dark environment. A directional green light
tracks the transaction's direction and a restrained brass tone marks the human
decision. Fine substrate grain stays behind all content. There are no drifting
gradient blobs, centered glow halos, full-page graph-paper grid, purple/blue
gradient, cream editorial surface, or default gray section bands.

## First Viewport Composition

The nav remains compact and uses the real Errand mark, a quiet sign-in link, and
one registration action. It is visually integrated into the composition rather
than floating as a pill.

The hero is composed around one signature artifact: a horizontal transaction
path spanning the content width. It is not a left-copy/right-panel split and not
a headline/subline/two-button stack.

The display statement occupies the upper-left and stays within two lines:

> Give it the errand. Keep the final say.

Supporting copy is reduced to a short explanation of policy, cart building, and
the human stop. One primary action starts an errand; sign-in remains in the nav.

Below and partly crossing the display field, the transaction path carries four
real stages:

1. `intent.received` — the spoken office-pantry request.
2. `policy.checked` — budget and merchant constraints.
3. `cart.ready` — three named products and a real demonstration total.
4. `approval.required` — a visibly closed human gate.

The path uses a bespoke continuous line with bends and gate geometry rather than
four cards or numbered steps on a vertical rail. Labels are typographic and bare;
icons are limited to custom SVG marks that communicate direction or the gate.

## Transaction Interaction

The transaction is visible and fully legible in its held state before any
animation starts.

When the page settles, a tonal tracer may travel along the already-visible path
from intent to the gate. It cannot gate content visibility. The tracer stops at
the gate and the gate gives a short, restrained mechanical response.

The approval area shows the exact demonstration merchant, total, and safety copy.
The primary control is `Approve demo`; decline is a quiet text control, not an
outlined twin.

On approval:

- The gate opens with a short authored SVG/CSS motion.
- The tracer completes the last segment.
- The state becomes `Approved demo`; the total and labels remain stable.
- A compact completion receipt replaces the controls without shifting the page.

On decline:

- The tracer retracts only from the gate segment.
- The state becomes `Declined`; copy states that nothing was charged.
- The geometry does not shake, bounce, glow, or jump.

After either decision, `Replay demo` resets the same artifact. All controls are
keyboard-accessible and expose the current state through text and an appropriate
live region.

Reduced-motion mode removes the traveling tracer and gate motion while retaining
the same immediate state changes and complete content.

## Remaining Page

The lower page develops the same transaction artifact rather than stacking
generic feature cards.

### Evidence Strip

A dense audit excerpt presents the real event vocabulary and demonstrates that
approval follows evidence. It is arranged as a responsive event ledger with
shared baselines, not a numbered process beside a rule. The final approval event
is the only emphasized row.

### Voice Passage

Voice is shown as a short transcript-to-action passage using the same transaction
language. A small custom waveform path can respond to pointer position or run a
quiet loop, but the transcript is visible by default and the animation stops for
reduced motion. No fake app window or generic orb is added to the landing page.

### Footer

The existing hackathon disclosure, real repository link, sign-in, and register
links remain. The oversized `ERRAND` signature is retained only if it is measured
again at desktop and mobile so no glyph is clipped. It stays above the substrate
texture and flush to the bottom edge.

## Responsive Behavior

At wide widths, the transaction path travels horizontally and the approval gate
owns the right third of the first viewport. At narrow widths, it becomes a custom
stepped vertical path, not four stacked cards. Corresponding labels remain aligned
to the path; the amount and controls stay in normal flow with touch targets at
least 44 CSS pixels high.

The first viewport is deliberately composed at common laptop dimensions. On
mobile, the hero may exceed one viewport when required for legibility; it must not
show a clipped fragment of the next section merely to force a nominal full-screen
height.

## SSR Session Mirror

The SSR change follows the approved low-risk Option B:

1. Add a same-origin Next route handler at `/api/session`.
2. `POST` accepts the JWT the signed-in browser already possesses and stores it as
   a first-party `httpOnly`, `secure` in production, `sameSite=lax`, path `/`
   cookie with a seven-day maximum age matching the current JWT lifetime.
3. `DELETE` expires that cookie.
4. Login, registration, localStorage hydration, and logout synchronize this cookie
   without making cookie synchronization a prerequisite for client authentication.
5. `/c/[id]` reads the cookie in its server component and requests the existing
   backend conversation endpoint with `Authorization: Bearer <token>`.
6. Successful data is passed as `initialDetail` into `ChatView` and `useChat`.
7. Missing, stale, or rejected cookies produce `initialDetail=null`; the existing
   client-side Bearer fetch remains the fallback.

This does not claim to move the JWT out of localStorage or remove XSS exposure.
It adds SSR only. The browser's chat SSE POST, approval POST, conversation
mutations, voice-ticket mint, and voice WebSocket remain direct to the backend.

## Error Handling

- Cookie synchronization failures do not sign the user out or block login.
- Server-side conversation fetches treat all non-2xx responses and network errors
  as a nullable SSR miss; no backend exception text is rendered.
- A seeded conversation remains visible while client auth hydrates, avoiding an
  empty flash. Once hydration completes, the existing client fetch can refresh
  canonical data.
- Landing demo state is local and cannot call the real purchasing orchestrator.

## Files Expected to Change

- `errand/frontend/app/page.tsx` — landing composition and interaction.
- `errand/frontend/app/landing.anim.css` — landing-only keyframes, if needed.
- `errand/frontend/app/globals.css` — only brand tokens/base changes that are
  genuinely shared; no page-specific CSS beyond keyframes.
- `errand/frontend/app/api/session/route.ts` — first-party cookie mirror.
- `errand/frontend/lib/auth.tsx` — best-effort session-cookie synchronization.
- `errand/frontend/app/(chat)/c/[id]/page.tsx` — server-side seed fetch.
- `errand/frontend/app/(chat)/c/page.tsx` — explicit null seed for a new chat if
  required by the prop contract.
- `errand/frontend/app/(chat)/ChatView.tsx` — accept initial detail.
- `errand/frontend/lib/useChat.ts` — seed messages without an auth-hydration flash.
- Focused frontend tests for cookie synchronization and seeded chat behavior.
- `CONTEXT.md` and local handoff files after verification.

No backend provider call, model constant, migration, SSE endpoint, WebSocket path,
or approval-gate behavior changes in this work.

## Verification

1. `bunx tsc --noEmit`.
2. `bunx vitest run` with focused tests for session sync and seeded history.
3. `bunx next build` and OpenNext build/deploy through CI.
4. Browser check at desktop and mobile widths:
   - first viewport composition;
   - no clipping or horizontal overflow;
   - approve, decline, and replay via real pointer clicks;
   - keyboard focus and state announcements;
   - reduced-motion behavior;
   - sign-in/register/source links.
5. Authenticated SSR round trip after deploy:
   - register or use a throwaway `example.com` account;
   - set the Worker-origin session cookie;
   - fetch a real owned `/c/[id]` and confirm the latest message content is in
     server HTML;
   - confirm direct SSE and voice-ticket requests still use their existing paths.
6. Run the anti-slop law point by point and correct every detected visual or
   interaction defect before reporting completion.

## Explicit Non-Goals

- No real purchase or Prava passkey run without separate explicit confirmation.
- No full backend-for-frontend proxy.
- No removal of localStorage JWT storage.
- No SSE or WebSocket proxy through Cloudflare Workers.
- No fake customer proof, metrics, product screenshot, or completed order.
- No new component library or Tailwind configuration change.
