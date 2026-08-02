"use client";

/* The front door.
 *
 * Composition note, because the default was deliberately avoided: this is not a
 * text column with a product panel beside it. The fold is a headline sitting
 * WITH one page-scale signature object — the transaction path in
 * <TransactionGate/> — as a single composition, the statement above and the
 * route beneath it. The path is the product's whole thesis in one object: a real
 * cart, a real total, four labelled stages, and a gate that stays shut until a
 * human opens it.
 *
 * The Approve / Decline / Replay controls are live. They are a demonstration,
 * labelled as one, and they resolve through a pure reducer that cannot reach any
 * purchasing code — nothing on this page looks interactive without being
 * interactive, and nothing here can move real money.
 *
 * Signed-in visitors never see any of it; they go straight to their chats.
 *
 * Styling note: this page is built on the app's own palette (declared in
 * globals.css) so the page and the product are one surface, not a marketing skin
 * bolted onto a different brand. The page background comes from globals.css
 * (authored directional light + grain on the substrate) and every section here
 * sits on it transparently, so colour carries straight through the scroll with
 * no seam anywhere.
 *
 * Deliberate absences: no pills, no gradient fills, no glow behind anything, no
 * icon-in-a-tile, no decorative hairlines, no card that lifts on hover, no
 * growing underline, no button that jumps when you point at it, no filled +
 * outlined button pair. Emphasis is a tonal step, never a saturated pop. */

import type { CSSProperties } from "react";
import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import "@/app/landing.anim.css";
import { useAuth } from "@/lib/auth";
import { ErrandMark } from "@/components/Marks";
import TransactionGate from "@/components/landing/TransactionGate";

// The responsive gutter every section reads from, kept as one custom property on
// the page root exactly as before.
const PAGE_VARS = { "--gutter": "clamp(20px, 5vw, 64px)" } as CSSProperties;

/* Sections open with a full sentence at display scale. No kicker above a
   heading, no small label over a big line. */
const SECTION = "max-w-page mx-auto pt-[clamp(72px,13vh,132px)] px-[var(--gutter)] pb-0";
const SECTION_TITLE =
  "font-display font-normal text-[clamp(26px,3.4vw,44px)] leading-[1.16] tracking-[-0.01em] text-hi m-0 max-w-[17em]";
const SECTION_BODY =
  "mt-5 mb-0 mx-0 text-[clamp(15px,1.2vw,16.5px)] leading-[1.65] text-mid max-w-[58ch]";
/* The one action. Solid, self-coloured edge, no glow, no radius theatrics, and
   it does not move when you point at it — the surface warms instead. The nav
   carries the same treatment at its own slightly tighter size, so the page
   speaks with one voice. */
const ACTION =
  "text-on-accent bg-green-soft no-underline text-[14.5px] [font-weight:560] px-[18px] py-[11px] rounded-chip transition-[background-color] duration-[160ms] ease-[ease] hover:bg-green";
const NAV_ACTION =
  "text-on-accent bg-green-soft no-underline text-[14.5px] [font-weight:560] px-4 py-[9px] rounded-chip transition-[background-color] duration-[160ms] ease-[ease] hover:bg-green";
const FOOTER_LINK =
  "text-[13.5px] text-mid no-underline transition-[color] duration-[160ms] ease-[ease] hover:text-hi";

// The audit trail, in the real backend event names. Set as data (font-mono) —
// the one place a mono belongs. Rows are separated by tone + the self-coloured
// lip, never a rail. The last row is the point of the section.
const AUDIT_EVENTS: readonly (readonly [string, string])[] = [
  ["run.started", "Intent accepted, profile resolved"],
  ["context.loaded", "Spend policy and approved merchants read"],
  ["cart.built", "Lines priced and totalled inside the ceiling"],
  ["payment.session", "Card session pinned to this merchant and total"],
  ["approval.request", "Stopped. Waiting on a human."],
];

export default function Landing() {
  const router = useRouter();
  const { user, loading } = useAuth();

  // A signed-in operator has no business on the front door.
  useEffect(() => {
    if (!loading && user) router.replace("/c");
  }, [loading, user, router]);

  return (
    <main className="relative z-[1]" style={PAGE_VARS}>
      {/* Treated, not a default row: the wordmark and the actions sit on the
          same optical baseline inside the page's own gutter, at max width. */}
      <header className="max-w-page mx-auto pt-[26px] px-[var(--gutter)] pb-0 flex items-center justify-between gap-5">
        <span className="inline-flex items-center gap-[9px]">
          <span className="inline-flex text-green-soft">
            <ErrandMark size={22} />
          </span>
          <span className="font-display text-[21px] [@media(max-width:560px)]:text-[19px] tracking-[0.005em] text-hi">
            Errand
          </span>
        </span>
        <nav className="flex items-center gap-[clamp(14px,3vw,28px)]">
          <Link
            className="text-mid no-underline text-[14.5px] transition-[color] duration-[160ms] ease-[ease] hover:text-hi"
            href="/login"
          >
            Sign in
          </Link>
          <Link className={NAV_ACTION} href="/register">
            Start an errand
          </Link>
        </nav>
      </header>

      {/* ── The fold ─────────────────────────────────────────────────────────
          One composition that owns the first screen: the statement, then the
          page-scale transaction path running the full content width beneath it.
          No half-section peeks in — the fold is sized to the viewport and
          balanced around its two elements. */}
      <section className="max-w-page mx-auto pt-[clamp(48px,9vh,92px)] px-[var(--gutter)] pb-[clamp(56px,10vh,104px)] min-h-[calc(100dvh-72px)] flex flex-col justify-center gap-[clamp(40px,6.5vh,68px)] [@media(max-width:560px)]:min-h-[auto] [@media(max-width:560px)]:pt-[clamp(36px,7vh,56px)]">
        <div>
          {/* Held to two lines at every width. A display line that stacks into
              three or four short rows is a staircase, not a composition; the
              tonal emphasis owns a whole line rather than straddling a break. */}
          <h1 className="font-display font-normal text-[clamp(38px,6vw,78px)] leading-[1.04] tracking-[-0.015em] text-hi m-0">
            <span className="block">Nothing moves</span>
            <span className="block text-mid">until you say so.</span>
          </h1>
          <p className="mt-[clamp(16px,2.4vh,24px)] mb-0 mx-0 text-[clamp(15px,1.25vw,17px)] leading-[1.6] text-mid max-w-[48ch]">
            Errand shops an approved merchant in plain words, builds the cart
            against your policy, and stops at a human gate before anything is
            charged.
          </p>
          <Link className={`inline-block mt-[clamp(22px,3vh,30px)] ${ACTION}`} href="/register">
            Start an errand
          </Link>
        </div>

        {/* The signature object — one responsive SVG transaction path with its
            live cart, total, and gate. This is what the headline sits with. */}
        <TransactionGate />
      </section>

      {/* ── The record ───────────────────────────────────────────────────── */}
      <section className={SECTION}>
        <h2 className={SECTION_TITLE}>
          Every step is on the record before you approve anything.
        </h2>
        <p className={SECTION_BODY}>
          The agent emits an audit event at each stage — what policy it loaded,
          what it put in the cart, what the payment session covers. The approval
          screen is the end of that list, not a dialog that appears out of
          nowhere.
        </p>

        <ol className="list-none mt-[clamp(30px,5vh,46px)] mb-0 mx-0 p-0 grid gap-0.5 max-w-[720px]">
          {AUDIT_EVENTS.map(([event, detail]) => (
            <li
              key={event}
              className="group grid grid-cols-[minmax(150px,210px)_1fr] gap-[clamp(14px,2.4vw,30px)] items-baseline py-[13px] shadow-[inset_0_1px_0_0_var(--color-edge)] [@media(max-width:640px)]:grid-cols-[1fr] [@media(max-width:640px)]:gap-[5px]"
            >
              <span className="font-mono text-[13px] text-green-dim group-last:text-green-soft">
                {event}
              </span>
              <span className="text-[14.5px] text-mid group-last:text-body">
                {detail}
              </span>
            </li>
          ))}
        </ol>
      </section>

      {/* ── Voice ────────────────────────────────────────────────────────────
          Errand is voice-first, so this section speaks in the same transaction
          language: a spoken request, a live transcript line, and ONE bespoke SVG
          waveform — not an icon tile, not a fake app window. */}
      <section className={SECTION}>
        <h2 className={SECTION_TITLE}>You can also just say it.</h2>
        <p className={SECTION_BODY}>
          Speak the errand, hear it think, and answer the approval the same way
          you started it. The transcript and the tool record land in the same
          thread as a typed conversation, so nothing about the run is only in the
          air.
        </p>

        <div className="mt-[clamp(30px,5vh,46px)] max-w-[720px] flex flex-col gap-[18px]">
          <VoiceWave />
          <p className="m-0 flex items-baseline gap-[10px] flex-wrap">
            <span className="font-mono text-[11px] tracking-[0.14em] uppercase text-green-dim shrink-0">
              You
            </span>
            <span className="text-[clamp(16px,1.7vw,20px)] leading-[1.5] text-body font-display">
              “Restock the office pantry under $100, approved brands only.”
            </span>
          </p>
        </div>

        <Link className={`inline-block mt-8 ${ACTION}`} href="/register">
          Start an errand
        </Link>
      </section>

      {/* ── Footer ───────────────────────────────────────────────────────────
          The wordmark is anchored flush to the bottom with nothing beneath it,
          on the layer above the page texture, and the links sit above it. */}
      <footer className="relative max-w-page mt-[clamp(96px,16vh,168px)] mx-auto mb-0 px-[var(--gutter)] overflow-hidden">
        <div className="flex flex-wrap items-baseline justify-between gap-y-5 gap-x-10 pb-[clamp(28px,5vh,48px)] [@media(max-width:560px)]:flex-col [@media(max-width:560px)]:items-start">
          <span className="text-[13px] text-low max-w-[42ch]">
            Built for the Agentic Commerce Hackathon. Payments run against the
            Prava sandbox.
          </span>
          <nav className="flex gap-[clamp(14px,2.6vw,28px)] flex-wrap">
            <Link className={FOOTER_LINK} href="/login">
              Sign in
            </Link>
            <Link className={FOOTER_LINK} href="/register">
              Create an account
            </Link>
            <a className={FOOTER_LINK} href="https://github.com/karanxa1/errand">
              Source
            </a>
          </nav>
        </div>
        {/* Full-width, tracked out, sitting on its own baseline at the very
            bottom edge. ERRAND has no descenders, so a baseline flush with the
            container clips nothing — the caps keep their full height with room
            above them. */}
        <span
          className="block font-display text-[clamp(56px,15.4vw,214px)] leading-[1] tracking-[0.055em] text-center text-ink-250 select-none pt-[0.06em] -mb-[0.082em]"
          aria-hidden="true"
        >
          ERRAND
        </span>
      </footer>
    </main>
  );
}

/* The voice waveform — one bespoke SVG, drawn in the brand's tonal green. Not an
   icon and not a fake app chrome: a real spoken utterance rendered as amplitude
   bars with rounded caps, quieter at the edges (silence into speech into
   silence). A single tracer dot rides across it, animating its x position — a
   decorative motion on an element that is already fully drawn, and it stops
   under prefers-reduced-motion. */
function VoiceWave() {
  // Deterministic amplitudes shaped like a real phrase: a rise, a couple of
  // stressed peaks, and a fall. Fixed data, not random, so the mark is stable.
  const amps = [
    0.18, 0.3, 0.52, 0.74, 0.62, 0.9, 0.68, 0.44, 0.58, 0.82, 0.96, 0.7, 0.5,
    0.66, 0.86, 0.6, 0.4, 0.55, 0.72, 0.48, 0.32, 0.5, 0.66, 0.42, 0.26, 0.36,
    0.22, 0.14,
  ];
  const w = 720;
  const h = 96;
  const mid = h / 2;
  const gap = w / amps.length;
  const barW = 3;
  const maxBar = mid - 8;

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      className="w-full h-auto overflow-visible text-green-dim"
      role="img"
      aria-label="Waveform of a spoken request"
    >
      {amps.map((a, i) => {
        const x = i * gap + gap / 2;
        const barH = Math.max(barW, a * maxBar);
        // The two loudest bars carry the live accent; the rest are the dim tone.
        const loud = a > 0.85;
        return (
          <line
            key={i}
            x1={x}
            y1={mid - barH}
            x2={x}
            y2={mid + barH}
            stroke="currentColor"
            strokeWidth={barW}
            strokeLinecap="round"
            className={loud ? "text-green-soft" : undefined}
          />
        );
      })}
      {/* the tracer dot rides left→right along the utterance. Driven by a CSS
          transform keyframe (not SMIL) so prefers-reduced-motion actually stops
          it; it rests at the start of the phrase when motion is reduced. */}
      <circle
        cx={0}
        cy={mid}
        r="3.4"
        fill="currentColor"
        className="text-green-soft [transform-box:view-box] animate-[voice-scan_3200ms_ease-in-out_infinite] motion-reduce:animate-none"
      />
    </svg>
  );
}
