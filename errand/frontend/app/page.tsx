"use client";

/* The front door.
 *
 * Composition note, because the default was deliberately avoided: this is not a
 * text column with a product panel beside it. The fold is a headline and then
 * ONE horizontal band — an open hold, laid out as page-scale type rather than
 * inside a card — running the full width beneath it. The band is the product's
 * whole thesis in one object: a real cart, a real total, and a stop.
 *
 * The Approve / Decline controls are live. They are a demonstration, labelled as
 * one, and they actually resolve — nothing on this page looks interactive
 * without being interactive.
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
 * growing underline, no button that jumps when you point at it. Emphasis is a
 * tonal step, never a saturated pop. */

import type { CSSProperties } from "react";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth";
import { ErrandMark } from "@/components/Marks";

// The demonstration cart. Fixed, small, and honest: a plausible office restock,
// not a claim about anyone's actual order.
const DEMO_LINES = [
  { label: "Oat milk", detail: "6 × 1L", amount: 23.4 },
  { label: "Dark roast beans", detail: "1kg", amount: 28.0 },
  { label: "Sparkling water", detail: "24 × 330ml", amount: 19.6 },
];
const DEMO_TOTAL = DEMO_LINES.reduce((sum, line) => sum + line.amount, 0);

const money = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD" });

type HoldState = "held" | "approved" | "declined";

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

export default function Landing() {
  const router = useRouter();
  const { user, loading } = useAuth();

  // A signed-in operator has no business on the front door.
  useEffect(() => {
    if (!loading && user) router.replace("/c");
  }, [loading, user, router]);

  const [hold, setHold] = useState<HoldState>("held");
  const resetHold = useCallback(() => setHold("held"), []);

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
          One composition: the statement, then a single horizontal band that
          runs the full content width beneath it. */}
      <section className="max-w-page mx-auto pt-[clamp(56px,11vh,108px)] px-[var(--gutter)] pb-[clamp(64px,12vh,120px)] min-h-[calc(100dvh-72px)] flex flex-col justify-center gap-[clamp(44px,7vh,76px)] [@media(max-width:560px)]:min-h-[auto] [@media(max-width:560px)]:pt-[clamp(40px,8vh,64px)]">
        <div>
          {/* Held to two lines at every width. A display line that stacks into
              three or four short rows is a staircase, not a composition. */}
          <h1 className="font-display font-normal text-[clamp(40px,6.4vw,82px)] leading-[1.03] tracking-[-0.015em] text-hi m-0">
            {/* Each line is its own block, so the display line is TWO lines at
                every width instead of wrapping into a three- or four-row
                staircase — and the tonal emphasis owns a whole line rather than
                straddling a break, which is what made it read as a stray
                coloured word. */}
            <span className="block">Nothing moves</span>
            <span className="block text-mid">until you say so.</span>
          </h1>
          <p className="mt-[clamp(18px,2.6vh,26px)] mb-0 mx-0 text-[clamp(15px,1.25vw,17px)] leading-[1.62] text-mid max-w-[54ch]">
            Errand takes a purchase in plain words, shops an approved merchant,
            builds the cart against your policy and pins a payment session. Then
            it stops — and waits for a human.
          </p>
        </div>

        {/* The hold, as page-scale type rather than a card: no panel fill, no
            drawn border, no shadow. The only rule on the page is structural
            rather than ornamental — the self-coloured lip that separates the
            statement from the ledger under it. */}
        <div
          className="group grid grid-cols-[minmax(210px,1fr)_minmax(280px,1.35fr)_minmax(230px,0.95fr)] gap-[clamp(28px,4vw,56px)] items-start pt-[clamp(26px,4vh,40px)] shadow-[inset_0_1px_0_0_var(--color-edge)] [@media(max-width:900px)]:grid-cols-[1fr] [@media(max-width:900px)]:gap-[30px]"
          data-state={hold}
        >
          <div className="flex flex-col gap-[9px]">
            <span className="font-mono text-[11.5px] tracking-[0.08em] uppercase text-green-dim transition-[color] duration-[220ms] ease-[ease] group-data-[state=approved]:text-green-soft group-data-[state=declined]:text-danger">
              {hold === "held"
                ? "Hold open"
                : hold === "approved"
                  ? "Settled"
                  : "Declined"}
            </span>
            <span className="font-display text-[clamp(22px,2.3vw,29px)] leading-[1.15] text-hi">
              Northwind Provisions
            </span>
            <span className="text-[13px] leading-[1.5] text-low max-w-[30ch]">
              A demonstration of the gate. No real order, no real card.
            </span>
          </div>

          <ul className="list-none m-0 p-0 flex flex-col gap-3">
            {DEMO_LINES.map((line) => (
              <li
                key={line.label}
                className="grid grid-cols-[1fr_auto] gap-x-[18px] items-baseline"
              >
                <span className="col-start-1 row-start-1 text-body text-[15px]">
                  {line.label}
                </span>
                <span className="col-start-1 row-start-2 text-low text-[12.5px]">
                  {line.detail}
                </span>
                <span className="col-start-2 row-start-1 row-span-2 self-center font-mono text-[14px] text-mid tabular-nums">
                  {money(line.amount)}
                </span>
              </li>
            ))}
          </ul>

          <div className="flex flex-col items-start gap-1.5 [@media(max-width:900px)]:pt-1.5 [@media(max-width:900px)]:shadow-[inset_0_1px_0_0_var(--color-edge)]">
            {/* Big type needs air. The total is the loudest thing on the page,
                so it gets room rather than tight tracking. */}
            <span className="font-display text-[clamp(42px,5.2vw,66px)] leading-[1.02] tracking-[-0.01em] text-hi tabular-nums transition-[color] duration-[260ms] ease-[ease] group-data-[state=declined]:text-low [@media(max-width:900px)]:pt-[18px]">
              {money(DEMO_TOTAL)}
            </span>
            <span className="text-[13.5px] text-mid min-h-[20px]">
              {hold === "held"
                ? "held until you approve"
                : hold === "approved"
                  ? "approved by you, then charged"
                  : "released — nothing was charged"}
            </span>

            {hold === "held" ? (
              <div className="flex items-center gap-4 mt-4 flex-wrap">
                <button
                  type="button"
                  className={`border-none ${ACTION}`}
                  onClick={() => setHold("approved")}
                >
                  Approve with passkey
                </button>
                {/* Not an outlined twin of the primary — a quiet text control,
                    which is what declining actually is. */}
                <button
                  type="button"
                  className="border-none bg-transparent px-0.5 py-[11px] text-mid text-[14.5px] transition-[color] duration-[160ms] ease-[ease] hover:text-hi"
                  onClick={() => setHold("declined")}
                >
                  Decline
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-4 mt-4 flex-wrap">
                <button
                  type="button"
                  className="border-none bg-transparent px-0.5 py-[11px] text-mid text-[14.5px] transition-[color] duration-[160ms] ease-[ease] hover:text-hi"
                  onClick={resetHold}
                >
                  Run it again
                </button>
              </div>
            )}
          </div>
        </div>
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

        {/* Real event names, set as data — the one place a mono belongs. Rows
            are separated by tone and rhythm, not by rules running down a rail.
            The last row is the point of the section, so it carries the page's
            live tone rather than the dimmed one. */}
        <ol className="list-none mt-[clamp(30px,5vh,46px)] mb-0 mx-0 p-0 grid gap-0.5 max-w-[720px]">
          {[
            ["run.started", "Intent accepted, profile resolved"],
            ["context.loaded", "Spend policy and approved merchants read"],
            ["cart.built", "Three lines, priced, inside the ceiling"],
            ["payment.session", "Card session pinned to this merchant and total"],
            ["approval.request", "Stopped. Waiting on a human."],
          ].map(([step, detail]) => (
            <li
              key={step}
              className="group grid grid-cols-[minmax(150px,210px)_1fr] gap-[clamp(14px,2.4vw,30px)] items-baseline py-[13px] shadow-[inset_0_1px_0_0_var(--color-edge)] [@media(max-width:900px)]:grid-cols-[1fr] [@media(max-width:900px)]:gap-[5px]"
            >
              <span className="font-mono text-[13px] text-green-dim group-last:text-green-soft">
                {step}
              </span>
              <span className="text-[14.5px] text-mid group-last:text-body">
                {detail}
              </span>
            </li>
          ))}
        </ol>
      </section>

      {/* ── Voice ────────────────────────────────────────────────────────── */}
      <section className={SECTION}>
        <h2 className={SECTION_TITLE}>You can also just say it.</h2>
        <p className={SECTION_BODY}>
          Errand is voice-first. Speak the errand, hear it think, and answer the
          approval the same way you started it. The transcript and the tool
          record land in the same thread as a typed conversation, so nothing
          about the run is only in the air.
        </p>
        <Link className={`inline-block mt-7 ${ACTION}`} href="/register">
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
