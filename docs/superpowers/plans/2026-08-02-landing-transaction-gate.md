# Landing Transaction Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current restrained landing page with a distinctive, responsive transaction-path artifact that visibly moves a purchase through policy and cart preparation, stops at a human gate, and responds to approve, decline, and replay.

**Architecture:** `app/page.tsx` owns page composition and signed-in redirect. A focused `TransactionGate` client component owns the signature SVG/path, decision controls, and state. A small pure state module makes every interaction testable; landing-only keyframes live in one `*.anim.css` file while all layout and visual styling stays in literal Tailwind v4 classes.

**Tech Stack:** Next.js 15, React 19, TypeScript, Tailwind v4, bespoke SVG, CSS keyframes, Vitest, Testing Library, Chrome browser verification.

## Global Constraints

- Content is visible by default; no text or control starts at opacity 0.
- No generic split hero, fake app window, floating card, icon tile, glow halo, purple/blue gradient, or card hover lift.
- Headline stays within two composed lines at desktop and mobile.
- Motion communicates transaction progress and gate state; it stops under `prefers-reduced-motion`.
- Approve, decline, replay, sign-in, register, and source controls all work with pointer and keyboard.
- Demonstration data is labeled and cannot invoke the real purchasing orchestrator.
- Tailwind v4 only; `landing.anim.css` contains keyframes only.
- Keep Gambarino, the existing warm green-black palette, real repository link, and truthful hackathon disclosure.

---

### Task 1: Testable Landing Demo State

**Files:**
- Create: `errand/frontend/lib/landingDemo.ts`
- Create: `errand/frontend/lib/landingDemo.test.ts`

**Interfaces:**
- Produces: `LandingDemoState = "held" | "approved" | "declined"`.
- Produces: `landingDemoReducer(state, action)` where action is `approve | decline | replay`.
- Produces: immutable `DEMO_PURCHASE` data and `DEMO_TOTAL_CENTS`.

- [ ] **Step 1: Write the failing reducer and data tests**

```ts
import { describe, expect, it } from "vitest";
import { DEMO_PURCHASE, DEMO_TOTAL_CENTS, landingDemoReducer } from "./landingDemo";

describe("landing demo", () => {
  it("uses internally consistent line-item math", () => {
    expect(DEMO_TOTAL_CENTS).toBe(
      DEMO_PURCHASE.items.reduce((sum, item) => sum + item.unitCents * item.quantity, 0),
    );
  });

  it("supports approve, decline, and replay", () => {
    expect(landingDemoReducer("held", { type: "approve" })).toBe("approved");
    expect(landingDemoReducer("held", { type: "decline" })).toBe("declined");
    expect(landingDemoReducer("approved", { type: "replay" })).toBe("held");
    expect(landingDemoReducer("declined", { type: "replay" })).toBe("held");
  });
});
```

- [ ] **Step 2: Run the test and verify RED**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx vitest run lib/landingDemo.test.ts`

Expected: FAIL because `./landingDemo` does not exist.

- [ ] **Step 3: Implement immutable data and reducer**

```ts
export type LandingDemoState = "held" | "approved" | "declined";
export type LandingDemoAction = { type: "approve" | "decline" | "replay" };

export const DEMO_PURCHASE = {
  merchant: "Northwind Provisions",
  request: "Restock the office pantry under $100, approved brands only.",
  policy: "Approved merchant · Pantry budget $100",
  items: [
    { name: "Oat milk", detail: "6 × 1L", unitCents: 390, quantity: 6 },
    { name: "Dark roast beans", detail: "1kg", unitCents: 2800, quantity: 1 },
    { name: "Sparkling water", detail: "24 × 330ml", unitCents: 1960, quantity: 1 },
  ],
} as const;

export const DEMO_TOTAL_CENTS = DEMO_PURCHASE.items.reduce(
  (sum, item) => sum + item.unitCents * item.quantity,
  0,
);

export function landingDemoReducer(
  state: LandingDemoState,
  action: LandingDemoAction,
): LandingDemoState {
  if (action.type === "replay") return "held";
  if (state !== "held") return state;
  return action.type === "approve" ? "approved" : "declined";
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx vitest run lib/landingDemo.test.ts`

Expected: `2 passed`.

- [ ] **Step 5: Commit the landing state**

```bash
git add errand/frontend/lib/landingDemo.ts errand/frontend/lib/landingDemo.test.ts
git commit -m "test(frontend): define the landing transaction demo"
```

### Task 2: Signature Transaction Gate

**Files:**
- Create: `errand/frontend/components/landing/TransactionGate.tsx`
- Create: `errand/frontend/components/landing/TransactionGate.test.tsx`
- Create: `errand/frontend/app/landing.anim.css`

**Interfaces:**
- Consumes: `DEMO_PURCHASE`, `DEMO_TOTAL_CENTS`, and `landingDemoReducer`.
- Produces: `<TransactionGate />`, a self-contained non-spending interactive demonstration.

- [ ] **Step 1: Write interaction tests before the component**

```tsx
// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import TransactionGate from "./TransactionGate";

describe("TransactionGate", () => {
  it("shows complete held-state information by default", () => {
    render(<TransactionGate />);
    expect(screen.getByText("Approval required")).toBeTruthy();
    expect(screen.getByText("$71.00")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve demo" })).toBeTruthy();
  });

  it("approves and replays without invoking external work", () => {
    render(<TransactionGate />);
    fireEvent.click(screen.getByRole("button", { name: "Approve demo" }));
    expect(screen.getByText("Approved demo")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Replay demo" }));
    expect(screen.getByText("Approval required")).toBeTruthy();
  });

  it("declines and states that nothing was charged", () => {
    render(<TransactionGate />);
    fireEvent.click(screen.getByRole("button", { name: "Decline demo" }));
    expect(screen.getByText("Nothing was charged.")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run the test and verify RED**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx vitest run components/landing/TransactionGate.test.tsx`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Build the visible-by-default artifact**

Implement `TransactionGate.tsx` with:

```tsx
"use client";
import { useReducer } from "react";
import { DEMO_PURCHASE, DEMO_TOTAL_CENTS, landingDemoReducer } from "@/lib/landingDemo";
import "@/app/landing.anim.css";

const money = (cents: number) => `$${(cents / 100).toFixed(2)}`;

export default function TransactionGate() {
  const [state, dispatch] = useReducer(landingDemoReducer, "held");
  const held = state === "held";
  return (
    <section aria-label="Demonstration purchase approval" data-state={state}>
      <div aria-hidden="true">{/* one responsive bespoke SVG transaction path */}</div>
      <ol>{/* four visible stages: intent, policy, cart, approval */}</ol>
      <div aria-live="polite">
        <span>{held ? "Approval required" : state === "approved" ? "Approved demo" : "Declined demo"}</span>
        <strong>{money(DEMO_TOTAL_CENTS)}</strong>
        <span>{state === "declined" ? "Nothing was charged." : "Demonstration only. No real order or card."}</span>
        {held ? (
          <div>
            <button onClick={() => dispatch({ type: "approve" })}>Approve demo</button>
            <button onClick={() => dispatch({ type: "decline" })}>Decline demo</button>
          </div>
        ) : (
          <button onClick={() => dispatch({ type: "replay" })}>Replay demo</button>
        )}
      </div>
    </section>
  );
}
```

The component's complete visual markup must satisfy this contract:

- One SVG path with desktop and mobile CSS variants; do not render four cards.
- All four stage labels and values are ordinary visible HTML, aligned to the path.
- SVG uses `currentColor`, rounded caps, and a custom two-leaf gate silhouette.
- `data-state` drives tracer completion, gate opening, and tonal changes.
- Buttons remain stationary on hover; change color only.
- State areas reserve enough height so approve/decline/replay causes no layout jump.

- [ ] **Step 4: Add keyframes only**

`landing.anim.css` may contain only named keyframes such as:

```css
@keyframes transaction-trace {
  from { stroke-dashoffset: 1; }
  to { stroke-dashoffset: 0; }
}

@keyframes gate-settle {
  0%, 100% { transform: rotate(0); }
  45% { transform: rotate(3deg); }
}
```

All selectors, animation assignment, timing, responsive layout, and reduced-motion utilities stay as Tailwind classes in the component. Never animate opacity from 0.

- [ ] **Step 5: Run interaction tests and typecheck**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx vitest run components/landing/TransactionGate.test.tsx`

Expected: `3 passed`.

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx tsc --noEmit`

Expected: exit 0.

- [ ] **Step 6: Commit the signature artifact**

```bash
git add errand/frontend/components/landing/TransactionGate.tsx errand/frontend/components/landing/TransactionGate.test.tsx errand/frontend/app/landing.anim.css
git commit -m "feat(frontend): build the landing transaction gate"
```

### Task 3: Compose the Full Landing Page

**Files:**
- Modify: `errand/frontend/app/page.tsx:1-312`

**Interfaces:**
- Consumes: `<TransactionGate />`, `useAuth`, and the existing `ErrandMark`.
- Preserves: authenticated-user redirect to `/c`.

- [ ] **Step 1: Replace the hero composition**

In `app/page.tsx`, retain `"use client"`, `useAuth`, and the signed-in redirect. Compose:

```tsx
<main className="relative z-[1] overflow-x-clip" style={PAGE_VARS}>
  <LandingNav />
  <section className="... first-viewport composition ...">
    <h1>
      <span className="block">Give it the errand.</span>
      <span className="block text-mid">Keep the final say.</span>
    </h1>
    <p>Speak or type the purchase. Errand checks policy, builds the cart, and stops before payment.</p>
    <Link href="/register">Start an errand</Link>
    <TransactionGate />
  </section>
  <EvidenceLedger />
  <VoicePassage />
  <LandingFooter />
</main>
```

Required composition rules:

- The first frame is not a split hero; the transaction path overlaps the display field as one page-scale composition.
- One primary action only; sign-in remains in nav.
- Headline stays two explicit block lines.
- Copy is shorter than the current page.
- Sections reuse the transaction language and do not become feature-card grids.
- The event ledger uses real event names already emitted by the backend.
- Voice passage contains a visible transcript and one custom SVG waveform, not a fake app window or icon tile.
- Footer links remain real and the sandbox disclosure stays truthful.

- [ ] **Step 2: Implement mobile composition**

Use min-width-first Tailwind classes and explicit narrow-screen variants so:

- The transaction path becomes stepped/vertical below 760px.
- Labels remain in normal flow and no text sits on a clipped SVG edge.
- Approval controls are at least 44px high.
- No horizontal overflow occurs at 320px width.
- The footer wordmark has enough top and bottom allowance for Gambarino's measured ink and does not clip.

- [ ] **Step 3: Run static verification**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx tsc --noEmit && bunx vitest run`

Expected: typecheck exit 0 and all tests pass.

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx next build`

Expected: production build succeeds; `/` stays static.

- [ ] **Step 4: Commit page composition**

```bash
git add errand/frontend/app/page.tsx
git commit -m "feat(frontend): compose the transaction-led landing page"
```

### Task 4: Browser and Anti-Slop Verification

**Files:**
- Modify as defects are found: `errand/frontend/app/page.tsx`
- Modify as defects are found: `errand/frontend/components/landing/TransactionGate.tsx`
- Modify as defects are found: `errand/frontend/app/landing.anim.css`

- [ ] **Step 1: Start one fresh local server**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bun run dev -- -p 3011`

Expected: server listens on 3011. If `EADDRINUSE` occurs, stop the stale process before viewing anything.

- [ ] **Step 2: Desktop pointer test at 1440×1000**

With Chrome tooling:

1. Load `http://localhost:3011`.
2. Screenshot the first viewport and full page.
3. Click `Approve demo`, verify `Approved demo` and stable geometry.
4. Click `Replay demo`, click `Decline demo`, verify `Nothing was charged.`.
5. Click or inspect sign-in/register/source targets.
6. Check the console for errors and network for failed first-party assets.

- [ ] **Step 3: Mobile test at 390×844 and 320×700**

Verify no horizontal scrolling, no sliced labels, no clipped footer glyphs, complete amount and controls, at least 44px touch targets, and a deliberate stepped transaction path.

- [ ] **Step 4: Reduced-motion test**

Emulate `prefers-reduced-motion: reduce`, reload, and verify all copy, stages, amount, and controls are immediately visible and state changes still work without tracer/gate animation.

- [ ] **Step 5: Run the complete anti-slop audit**

Check every item in `/Users/macbook/.config/opencode/AGENTS.md`, with special attention to:

- no default hero stack or split hero;
- no content hidden behind entrance animation;
- no clipping near SVG/path cuts;
- no saturated accent sprayed across every label;
- no glowy button or all-around shadow;
- no ragged comparative alignment;
- centered controls and SVG gate geometry;
- continuous color surface with no hard section seams;
- real, working controls only;
- authored signature artifact and purposeful motion.

Fix every defect found, then repeat the relevant screenshot/click test.

- [ ] **Step 6: Stop the local server and commit audit corrections**

```bash
git add errand/frontend/app/page.tsx errand/frontend/components/landing/TransactionGate.tsx errand/frontend/app/landing.anim.css
git commit -m "fix(frontend): finish the landing interaction audit"
```

### Task 5: Integrated Release Verification

**Files:**
- Modify: `CONTEXT.md`
- Rewrite locally, do not commit: `errand-handoff.md`
- Rewrite locally, do not commit: `opencode-prompt.md`

- [ ] **Step 1: Run one authoritative frontend gate after merging both plans**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx tsc --noEmit && bunx vitest run && bunx next build`

Expected: typecheck exit 0, every old and new test passes, production build succeeds.

- [ ] **Step 2: Update project context**

Document the transaction-gate signature, the truthful local-only demo interaction, the first-party SSR cookie mirror, and the unchanged direct SSE/voice architecture.

- [ ] **Step 3: Commit and push only verified work**

Inspect `git status`, `git diff`, and `git log --oneline -10`; stage only intended application, tests, and `CONTEXT.md`. Never stage the gitignored handoff files or secrets.

- [ ] **Step 4: Poll both path-filtered workflows to terminal state**

Run: `gh run list --limit 4` until each triggered backend/frontend run is `completed`. For this frontend-only change, the frontend workflow must run and finish `success`; a backend workflow may not trigger due to path filtering.

- [ ] **Step 5: Verify deployed URLs rather than trusting CI**

Confirm `/`, `/c`, `/login`, `/register` return 200. In a browser, repeat approve/replay/decline on the deployed landing page at desktop and mobile widths. Confirm direct unauthenticated spend/voice endpoints still return 401.

- [ ] **Step 6: Verify SSR live with a safe throwaway account**

Use an `example.com` account, create or identify an owned conversation without triggering `run_errand`, mirror its token through the Worker session route, and request `/c/[id]` with the returned cookie. Confirm a known message string appears in response HTML. Do not send email or initiate a purchase.
