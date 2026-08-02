"use client";

/* The signature artifact.
 *
 * ONE bespoke inline SVG "transaction path" — not four cards. A single stroked
 * line threads four labelled stages (intent → policy → cart → approval) and
 * lands on a custom two-leaf gate that stays SHUT until a human approves. A
 * tracer runs the full length of that same line (animating stroke-dashoffset on
 * a set dasharray, so the caps never flip), a purely decorative motion on an
 * element that is already fully visible — the text and the total are never
 * gated behind it. Under prefers-reduced-motion the tracer is simply drawn in
 * place.
 *
 * data-state (held | approved | declined) drives three things: the tracer's
 * length, the gate opening, and a tonal colour step — green-dim/soft while held,
 * green-soft when approved, a dimmed danger tone when declined. Never a
 * saturated splash; the amount is the loudest thing and it is set in the display
 * face with room to breathe.
 *
 * Everything here is a demonstration, labelled as one. The reducer is a pure
 * function in lib/landingDemo — it cannot reach any purchasing code. */

import { useReducer } from "react";

import "@/app/landing.anim.css";
import { DEMO_PURCHASE, DEMO_TOTAL_CENTS, landingDemoReducer } from "@/lib/landingDemo";

const money = (cents: number) =>
  (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });

// The four stages, in order. The label sits in normal HTML flow aligned to its
// point on the path; the gloss is the plain-language read of each waypoint.
const STAGES = [
  { key: "intent", label: "Intent", gloss: "said in plain words" },
  { key: "policy", label: "Policy", gloss: "checked against your rules" },
  { key: "cart", label: "Cart", gloss: "built inside the ceiling" },
  { key: "approval", label: "Approval", gloss: "stopped for a human" },
] as const;

// Horizontal geometry (desktop). A single gentle path with a small rise at each
// waypoint, so it reads as motion with intent rather than a ruler line. The four
// x positions are the stage anchors; the gate sits just past the last one.
const H = {
  w: 720,
  h: 132,
  y: 74,
  xs: [70, 268, 466, 628],
  // A smooth path that dips through each waypoint node.
  d: "M18 74 C 120 74, 150 58, 268 58 C 386 58, 366 88, 466 88 C 560 88, 580 74, 628 74 L 662 74",
  gate: 662,
};

// Vertical geometry (mobile). The same idea turned down the page so labels never
// have to sit under a sliced SVG edge.
const V = {
  w: 120,
  h: 460,
  x: 44,
  ys: [40, 168, 296, 408],
  d: "M44 20 C 44 90, 78 100, 78 168 C 78 236, 44 246, 44 296 C 44 356, 44 372, 44 408 L 44 436",
  gate: 436,
};

export default function TransactionGate() {
  const [state, dispatch] = useReducer(landingDemoReducer, "held" as const);

  const statusLabel =
    state === "approved"
      ? "Approved demo"
      : state === "declined"
        ? "Declined demo"
        : "Approval required";

  // The line that separates the ledger from the controls holds the live tone.
  const toneRing =
    state === "approved"
      ? "text-green-soft"
      : state === "declined"
        ? "text-danger"
        : "text-green-dim";

  return (
    <section
      aria-label="Demonstration purchase approval"
      data-state={state}
      className="group/gate w-full"
    >
      {/* Header row: what this is, and the loud total. Labelled a demo so nothing
          here is mistaken for a real order. */}
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
        <div className="flex flex-col gap-2 max-w-[34ch]">
          <span className="font-mono text-[11px] tracking-[0.14em] uppercase text-low">
            Live demo · nothing real is charged
          </span>
          <span className="font-display text-[clamp(21px,2.4vw,28px)] leading-[1.14] text-hi">
            {DEMO_PURCHASE.merchant}
          </span>
          <span className="text-[13px] leading-[1.55] text-mid">
            {DEMO_PURCHASE.request}
          </span>
        </div>
        <div className="flex flex-col items-start gap-1">
          {/* The amount is the loudest element: display face, loose tracking,
              real air. It steps to a dim tone when declined. */}
          <span className="font-display leading-[1] text-[clamp(46px,6vw,74px)] tracking-[0.01em] tabular-nums text-hi transition-[color] duration-[280ms] ease-out group-data-[state=declined]/gate:text-low">
            {money(DEMO_TOTAL_CENTS)}
          </span>
          <span className="font-mono text-[11px] tracking-[0.1em] uppercase text-low">
            {DEMO_PURCHASE.policy}
          </span>
        </div>
      </div>

      {/* ── The transaction path ─────────────────────────────────────────────
          ONE stroked line through four stages, ending at a two-leaf gate. Two
          bespoke SVGs — horizontal for desktop, stepped for narrow — swapped by
          breakpoint so labels are never sliced by an SVG edge. */}
      <div className={`mt-[clamp(28px,4vh,44px)] ${toneRing} transition-[color] duration-[300ms] ease-out`}>
        {/* Desktop / wide: horizontal */}
        <div className="hidden min-[760px]:block">
          <div className="relative w-full">
            <svg
              viewBox={`0 0 ${H.w} ${H.h}`}
              className="w-full h-auto overflow-visible"
              role="presentation"
              aria-hidden="true"
            >
              {/* the resting line — the full route, quiet */}
              <path
                d={H.d}
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                opacity="0.28"
              />
              {/* the tracer — completes along the SAME line. Held: reaches the
                  gate but stays "waiting"; approved: full & bright; declined:
                  drawn but dimmed. stroke-dashoffset animates on a set dasharray
                  so the round caps never flip. */}
              <path
                d={H.d}
                fill="none"
                stroke="currentColor"
                strokeWidth="2.25"
                strokeLinecap="round"
                pathLength={1}
                strokeDasharray={1}
                className="[stroke-dashoffset:1] animate-[transaction-trace_1100ms_ease-out_forwards] motion-reduce:animate-none motion-reduce:[stroke-dashoffset:0]"
              />
              {/* stage nodes */}
              {H.xs.map((x, i) => {
                const isApproval = i === STAGES.length - 1;
                return (
                  <circle
                    key={x}
                    cx={x}
                    cy={i === 1 ? 58 : i === 2 ? 88 : 74}
                    r={isApproval ? 4.4 : 3.2}
                    fill="currentColor"
                    opacity={isApproval ? 1 : 0.55}
                  />
                );
              })}
              <Gate x={H.gate} y={H.y} />
            </svg>
            {/* stage labels in normal HTML flow, anchored under each node with
                padding that clears the SVG's drawn edge on both sides */}
            <ol className="list-none m-0 mt-4 p-0 grid grid-cols-4 gap-4">
              {STAGES.map((stage) => (
                <li key={stage.key} className="flex flex-col gap-1">
                  <span className="font-mono text-[12px] tracking-[0.04em] text-body">
                    {stage.label}
                  </span>
                  <span className="text-[12.5px] leading-[1.45] text-low">
                    {stage.gloss}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        </div>

        {/* Narrow: stepped / vertical. Labels sit to the right in flow, clear of
            any edge. */}
        <div className="block min-[760px]:hidden">
          <div className="grid grid-cols-[120px_1fr] gap-x-4">
            <svg
              viewBox={`0 0 ${V.w} ${V.h}`}
              className="w-full h-auto overflow-visible"
              role="presentation"
              aria-hidden="true"
            >
              <path
                d={V.d}
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                opacity="0.28"
              />
              <path
                d={V.d}
                fill="none"
                stroke="currentColor"
                strokeWidth="2.25"
                strokeLinecap="round"
                pathLength={1}
                strokeDasharray={1}
                className="[stroke-dashoffset:1] animate-[transaction-trace_1100ms_ease-out_forwards] motion-reduce:animate-none motion-reduce:[stroke-dashoffset:0]"
              />
              {V.ys.map((y, i) => {
                const isApproval = i === STAGES.length - 1;
                return (
                  <circle
                    key={y}
                    cx={i === 1 ? 78 : 44}
                    cy={y}
                    r={isApproval ? 4.4 : 3.2}
                    fill="currentColor"
                    opacity={isApproval ? 1 : 0.55}
                  />
                );
              })}
              <Gate x={V.x} y={V.gate} vertical />
            </svg>
            <ol className="list-none m-0 p-0 grid grid-rows-4">
              {STAGES.map((stage) => (
                <li key={stage.key} className="flex flex-col justify-center gap-0.5">
                  <span className="font-mono text-[12.5px] tracking-[0.04em] text-body">
                    {stage.label}
                  </span>
                  <span className="text-[12.5px] leading-[1.4] text-low">
                    {stage.gloss}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>

      {/* ── Ledger + controls ────────────────────────────────────────────────
          Separated from the path by the self-coloured lip, never a drawn rail. */}
      <div className="mt-[clamp(26px,4vh,40px)] pt-[clamp(20px,3vh,30px)] shadow-[inset_0_1px_0_0_var(--color-edge)] grid grid-cols-1 min-[760px]:grid-cols-[1fr_auto] gap-x-10 gap-y-6 items-start">
        <ul className="list-none m-0 p-0 flex flex-col gap-2.5 max-w-[46ch]">
          {DEMO_PURCHASE.items.map((item) => (
            <li key={item.name} className="grid grid-cols-[1fr_auto] gap-x-5 items-baseline">
              <span className="col-start-1 row-start-1 text-body text-[15px]">
                {item.name}
              </span>
              <span className="col-start-1 row-start-2 text-low text-[12.5px]">
                {item.detail}
              </span>
              <span className="col-start-2 row-start-1 row-span-2 self-center font-mono text-[13.5px] text-mid tabular-nums">
                {money(item.unitCents * item.quantity)}
              </span>
            </li>
          ))}
        </ul>

        {/* The live region. A generous min-height reserves space for every
            state's copy + controls, so approve/decline/replay never shifts the
            layout. */}
        <div
          aria-live="polite"
          className="flex flex-col items-start gap-3 min-w-[min(260px,100%)] min-h-[132px] min-[760px]:items-end min-[760px]:text-right"
        >
          <span
            className={`font-mono text-[11px] tracking-[0.14em] uppercase transition-[color] duration-[240ms] ease-out ${
              state === "approved"
                ? "text-green-soft"
                : state === "declined"
                  ? "text-danger"
                  : "text-green-dim"
            }`}
          >
            {statusLabel}
          </span>

          <p className="m-0 text-[14px] leading-[1.5] text-mid max-w-[30ch]">
            {state === "held" ? (
              "The route ran and stopped at the gate. Approve to open it."
            ) : state === "approved" ? (
              "You opened the gate. In production this is where a passkey settles the session."
            ) : (
              <>
                The gate stayed shut. <span>Nothing was charged.</span>
              </>
            )}
          </p>

          {state === "held" ? (
            <div className="flex items-center gap-4 flex-wrap min-[760px]:justify-end">
              {/* Primary: stationary on hover, warms in colour only. */}
              <button
                type="button"
                onClick={() => dispatch({ type: "approve" })}
                className="border-none text-on-accent bg-green-soft rounded-chip text-[14.5px] [font-weight:560] px-[18px] min-h-[44px] transition-[background-color] duration-[160ms] ease-out hover:bg-green"
              >
                Approve demo
              </button>
              {/* Decline is a quiet text control, not an outlined twin. */}
              <button
                type="button"
                onClick={() => dispatch({ type: "decline" })}
                className="border-none bg-transparent px-1 min-h-[44px] text-mid text-[14.5px] transition-[color] duration-[160ms] ease-out hover:text-hi"
              >
                Decline demo
              </button>
            </div>
          ) : (
            <div className="flex items-center min-[760px]:justify-end">
              <button
                type="button"
                onClick={() => dispatch({ type: "replay" })}
                className="border-none bg-transparent px-1 min-h-[44px] text-mid text-[14.5px] transition-[color] duration-[160ms] ease-out hover:text-hi"
              >
                Replay demo
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/* The gate — two leaves that meet at the end of the route and swing open only
   when the state is approved. Centred on (x, y). Drawn in currentColor so it
   takes the section's live tone. A small keystone node marks the meeting point
   and reads as the "human" at the gate. */
function Gate({
  x,
  y,
  vertical = false,
}: {
  x: number;
  y: number;
  vertical?: boolean;
}) {
  // Leaf half-length and the hinge offset from centre.
  const leaf = 15;
  const hinge = 3;
  // Horizontal gate: leaves are vertical bars that rotate about their base.
  // Vertical gate: rotate the whole group 90° so the same geometry reads down
  // the page.
  return (
    <g
      transform={vertical ? `rotate(90 ${x} ${y})` : undefined}
      className="[transform-box:fill-box]"
    >
      {/* upper leaf — hinges at its base and swings open on approve.
          transform-box: view-box makes transform-origin resolve in the SVG's
          coordinate space, so the hinge sits exactly on the meeting point. */}
      <line
        x1={x}
        y1={y - hinge}
        x2={x}
        y2={y - hinge - leaf}
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        style={{ transformBox: "view-box", transformOrigin: `${x}px ${y - hinge}px` }}
        className="transition-transform duration-[420ms] ease-out group-data-[state=approved]/gate:[transform:rotate(-34deg)] motion-reduce:transition-none"
      />
      {/* lower leaf */}
      <line
        x1={x}
        y1={y + hinge}
        x2={x}
        y2={y + hinge + leaf}
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        style={{ transformBox: "view-box", transformOrigin: `${x}px ${y + hinge}px` }}
        className="transition-transform duration-[420ms] ease-out group-data-[state=approved]/gate:[transform:rotate(34deg)] motion-reduce:transition-none"
      />
      {/* keystone — the point the human holds */}
      <circle cx={x} cy={y} r="2.6" fill="currentColor" />
    </g>
  );
}
