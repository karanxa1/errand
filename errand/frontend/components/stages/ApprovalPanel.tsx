"use client";

import { useState } from "react";
import type {
  ApprovalRequest,
  ApprovalResult,
  ToolCardProps,
} from "@/lib/types";
import { money } from "@/lib/format";
import "./stages.anim.css";

/* ApprovalPanel — the emotional peak, and the first typed tool card.

   It implements the tool-card contract ToolCardProps<ApprovalRequest,
   ApprovalResult> (shape borrowed from assistant-ui's tool-UI pattern — the
   INTERFACE only, not the library): it renders from its inbound `args`, its
   `result` (null until the operator decides), an explicit `status`
   (pending → resolving → resolved), and a single `resolve` callback that both
   the Approve and the (now live) "Not now" decline route through.

   Shows the cart total + merchant, mounts the Prava passkey iframe
   (session.iframe_url), and gates the spend on the operator's explicit verdict,
   which POSTs /approve { approved, reason? }. If the iframe can't render
   (sandbox blocks embedding), a clear fallback with the live link is shown so
   the moment still reads as "authorise on Prava". */

const WRAP =
  "relative rounded-panel p-[22px] bg-[image:radial-gradient(120%_90%_at_50%_-20%,rgba(232,180,95,0.08),transparent_60%),linear-gradient(180deg,var(--color-ink-150),var(--color-ink-100))] shadow-[inset_0_1px_0_rgba(232,180,95,0.14),inset_0_0_0_1px_var(--color-edge-strong)]";
const BADGE =
  "inline-flex items-center gap-[7px] text-[11px] tracking-[0.1em] uppercase font-semibold mb-3";
const HEADLINE =
  "font-display text-[27px] leading-[1.12] text-hi mt-0 mx-0 mb-1.5 tracking-[0.01em]";
const LEDE = "text-mid text-[14px] mt-0 mx-0 mb-5 max-w-[52ch]";
const TO_NAME = "text-hi font-semibold";

export default function ApprovalPanel({
  args: approval,
  result,
  status,
  resolve,
}: ToolCardProps<ApprovalRequest, ApprovalResult>) {
  const [frameFailed, setFrameFailed] = useState(false);
  const { cart, session, context } = approval;
  const merchant = context.approved_merchants[0];

  const resolving = status === "resolving";
  const declined = status === "resolved" && result?.approved === false;

  // Resolved-declined: the card holds its place with a clear, calm terminal
  // state instead of vanishing — the decision stays on the record.
  if (declined) {
    return (
      <div className={WRAP}>
        {/* Resolved-declined badge — a quiet, neutral close (not an error red). */}
        <span className={`${BADGE} text-mid`}>
          <span className="w-[7px] h-[7px] rounded-full bg-low" />
          Spend declined
        </span>
        <h2 className={HEADLINE}>You declined this spend</h2>
        <p className={LEDE}>
          Nothing was charged. The card session pinned to{" "}
          <span className={TO_NAME}>{merchant?.name ?? "the merchant"}</span>{" "}
          for {money(cart.total_cents)} was released.
        </p>
        {result?.reason && (
          <div className="flex flex-col gap-[3px] mt-[14px] px-[14px] py-3 rounded-chip bg-ink-050 shadow-[inset_0_0_0_1px_var(--color-edge)]">
            <span className="text-[10.5px] tracking-[0.12em] uppercase text-low font-semibold">
              Reason
            </span>
            <span className="text-[13.5px] text-body leading-[1.45]">
              {result.reason}
            </span>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={WRAP}>
      <span className={`${BADGE} text-brass`}>
        <span className="w-[7px] h-[7px] rounded-full bg-brass" />
        Human approval required
      </span>

      <h2 className={HEADLINE}>Authorise this spend</h2>
      <p className={LEDE}>
        The agent has a card session pinned to this exact merchant and amount.
        Confirm the passkey, then approve — nothing is charged until you do.
      </p>

      <div className="flex items-baseline gap-[14px] mb-[18px] flex-wrap">
        <span className="font-display text-[46px] leading-none text-hi tracking-[0.01em]">
          {money(cart.total_cents)}
        </span>
        <span className="text-[13px] text-mid">
          to <span className={TO_NAME}>{merchant?.name ?? "merchant"}</span>
          {" · "}
          {cart.items.length} item{cart.items.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="text-[11px] tracking-[0.12em] uppercase text-low mt-1 mb-[9px] flex items-center gap-2 font-semibold">
        <LockGlyph />
        Prava · passkey verification
      </div>

      {!frameFailed ? (
        /* The frame sits on a labelled substrate so it never reads as an empty
           void before/if the cross-origin Prava page paints. */
        <div className="relative w-full min-h-[220px] rounded-card overflow-hidden shadow-[inset_0_0_0_1px_var(--color-edge)] bg-ink-000">
          {/* Labelled substrate behind the frame — visible until (or if) the
              cross-origin Prava page paints, so this is never a blank void. */}
          <div
            className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-center p-6 z-0"
            aria-hidden="true"
          >
            <span className="w-[46px] h-[46px] rounded-full bg-ink-150 text-green inline-flex items-center justify-center shadow-[inset_0_0_0_1px_var(--color-edge)]">
              <LockGlyph />
            </span>
            <span className="text-[13px] text-body font-semibold">
              Prava secure session
            </span>
            <span className="text-[12px] text-low max-w-[40ch] leading-[1.5]">
              Card entry and passkey happen inside Prava&apos;s hosted frame,
              pinned to this merchant and amount.
            </span>
          </div>
          <iframe
            className="relative z-[1] w-full h-[320px] border-none rounded-card bg-transparent block"
            src={session.iframe_url}
            title="Prava passkey verification"
            onError={() => setFrameFailed(true)}
            allow="publickey-credentials-get *; payment *"
            sandbox="allow-scripts allow-forms allow-same-origin allow-popups"
          />
        </div>
      ) : (
        <div className="w-full min-h-[200px] rounded-card bg-ink-050 shadow-[inset_0_0_0_1px_var(--color-edge)] p-[18px] flex flex-col gap-3 justify-center">
          <div className="flex items-center gap-[9px] text-hi font-semibold text-[14px]">
            <LockGlyph />
            Verify on Prava
          </div>
          <div className="text-[12.5px] text-mid leading-[1.5]">
            The passkey step opens in Prava&apos;s secure session for this
            purchase.
          </div>
          <a
            className="font-mono text-[11.5px] text-green-soft break-all"
            href={session.iframe_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {session.iframe_url}
          </a>
        </div>
      )}

      <div className="flex items-center gap-[14px] mt-5 flex-wrap">
        {/* Approve — a single decisive action (not a filled+outline pair).
            Decline is a quiet text button, clearly subordinate, not a mirrored
            outline button. */}
        <button
          className="border-none bg-green text-on-accent [font-weight:680] text-[15px] px-[26px] py-[14px] rounded-xl inline-flex items-center gap-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.24)] transition-[background] duration-[180ms] ease-[ease] enabled:hover:bg-green-soft disabled:bg-ink-200 disabled:text-low disabled:shadow-[inset_0_0_0_1px_var(--color-edge)] disabled:cursor-default"
          onClick={() => resolve({ approved: true })}
          disabled={resolving}
        >
          {resolving ? (
            <>
              <span className="w-[15px] h-[15px] rounded-full border-2 border-[rgba(4,21,13,0.35)] border-t-[#04150d] animate-[errand-spin_0.7s_linear_infinite]" />
              Approving…
            </>
          ) : (
            <>
              <ShieldCheck />
              Approve {money(cart.total_cents)}
            </>
          )}
        </button>
        <button
          className="bg-transparent border-none text-low text-[13px] px-1 py-1.5 hover:text-body"
          disabled={resolving}
          type="button"
          onClick={() => resolve({ approved: false })}
        >
          Not now
        </button>
        <span className="text-[12px] text-low ml-auto">
          Session {session.session_id.slice(0, 14)}…
        </span>
      </div>
    </div>
  );
}

function LockGlyph() {
  return (
    <svg
      className="text-green"
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
    >
      <rect
        x="3.5"
        y="7"
        width="9"
        height="6.5"
        rx="1.6"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M5.5 7V5.2a2.5 2.5 0 0 1 5 0V7"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function ShieldCheck() {
  return (
    <svg width="17" height="17" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path
        d="M9 2l5.5 2v4.5c0 3.4-2.3 6-5.5 7C5.8 14.5 3.5 11.9 3.5 8.5V4L9 2Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M6.5 9l1.8 1.8L11.8 7"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
