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
 * Signed-in visitors never see any of it; they go straight to their chats. */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth";
import { ErrandMark } from "@/components/Marks";

import css from "./landing.module.css";

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
    <main className={css.page}>
      <header className={css.nav}>
        <span className={css.navBrand}>
          <span className={css.navMark}>
            <ErrandMark size={22} />
          </span>
          <span className={css.navName}>Errand</span>
        </span>
        <nav className={css.navLinks}>
          <Link className={css.navQuiet} href="/login">
            Sign in
          </Link>
          <Link className={css.navAction} href="/register">
            Start an errand
          </Link>
        </nav>
      </header>

      {/* ── The fold ─────────────────────────────────────────────────────── */}
      <section className={css.fold}>
        <div className={css.foldHead}>
          <h1 className={css.headline}>
            Nothing moves <span className={css.headlineTurn}>until you say so.</span>
          </h1>
          <p className={css.lede}>
            Errand takes a purchase in plain words, shops an approved merchant,
            builds the cart against your policy and pins a payment session. Then
            it stops — and waits for a human.
          </p>
        </div>

        {/* The hold, as page-scale type rather than a card. */}
        <div className={css.hold} data-state={hold}>
          <div className={css.holdMerchant}>
            <span className={css.holdStatus}>
              {hold === "held"
                ? "Hold open"
                : hold === "approved"
                  ? "Settled"
                  : "Declined"}
            </span>
            <span className={css.holdName}>Northwind Provisions</span>
            <span className={css.holdNote}>
              A demonstration of the gate. No real order, no real card.
            </span>
          </div>

          <ul className={css.holdLines}>
            {DEMO_LINES.map((line) => (
              <li key={line.label} className={css.holdLine}>
                <span className={css.holdLabel}>{line.label}</span>
                <span className={css.holdDetail}>{line.detail}</span>
                <span className={css.holdAmount}>{money(line.amount)}</span>
              </li>
            ))}
          </ul>

          <div className={css.holdTotal}>
            <span className={css.holdSum}>{money(DEMO_TOTAL)}</span>
            <span className={css.holdCaption}>
              {hold === "held"
                ? "held until you approve"
                : hold === "approved"
                  ? "approved by you, then charged"
                  : "released — nothing was charged"}
            </span>

            {hold === "held" ? (
              <div className={css.holdControls}>
                <button
                  type="button"
                  className={css.approve}
                  onClick={() => setHold("approved")}
                >
                  Approve with passkey
                </button>
                <button
                  type="button"
                  className={css.decline}
                  onClick={() => setHold("declined")}
                >
                  Decline
                </button>
              </div>
            ) : (
              <div className={css.holdControls}>
                <button type="button" className={css.decline} onClick={resetHold}>
                  Run it again
                </button>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ── The record ───────────────────────────────────────────────────── */}
      <section className={css.section}>
        <h2 className={css.sectionTitle}>
          Every step it took is written down before you are asked to approve
          anything.
        </h2>
        <p className={css.sectionBody}>
          The agent emits an audit event at each stage — what policy it loaded,
          what it put in the cart, what the payment session covers. The approval
          screen is the end of that list, not a dialog that appears out of
          nowhere.
        </p>

        <ol className={css.audit}>
          {[
            ["run.started", "Intent accepted, profile resolved"],
            ["context.loaded", "Spend policy and approved merchants read"],
            ["cart.built", "Three lines, priced, inside the ceiling"],
            ["payment.session", "Card session pinned to this merchant and total"],
            ["approval.request", "Stopped. Waiting on a human."],
          ].map(([step, detail]) => (
            <li key={step} className={css.auditRow}>
              <span className={css.auditStep}>{step}</span>
              <span className={css.auditDetail}>{detail}</span>
            </li>
          ))}
        </ol>
      </section>

      {/* ── Voice ────────────────────────────────────────────────────────── */}
      <section className={css.section}>
        <h2 className={css.sectionTitle}>
          You can also just say it out loud.
        </h2>
        <p className={css.sectionBody}>
          Errand is voice-first. Speak the errand, hear it think, and answer the
          approval the same way you started it. The transcript and the tool
          record land in the same thread as a typed conversation, so nothing
          about the run is only in the air.
        </p>
        <Link className={css.sectionAction} href="/register">
          Start an errand
        </Link>
      </section>

      {/* ── Footer ───────────────────────────────────────────────────────── */}
      <footer className={css.footer}>
        <div className={css.footerRow}>
          <span className={css.footerNote}>
            Built for the Agentic Commerce Hackathon. Payments run against the
            Prava sandbox.
          </span>
          <nav className={css.footerLinks}>
            <Link href="/login">Sign in</Link>
            <Link href="/register">Create an account</Link>
            <a href="https://github.com/karanxa1/errand">Source</a>
          </nav>
        </div>
        <span className={css.footerWordmark} aria-hidden="true">
          ERRAND
        </span>
      </footer>
    </main>
  );
}
