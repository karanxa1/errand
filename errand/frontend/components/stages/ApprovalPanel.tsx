"use client";

import { useState } from "react";
import type { ApprovalRequest } from "@/lib/types";
import { money } from "@/lib/format";
import s from "./ApprovalPanel.module.css";

/* ApprovalPanel — the emotional peak. Shows the cart total + merchant, mounts
   the Prava passkey iframe (session.iframe_url), and gates the spend on the
   operator's explicit Approve, which POSTs /approve {approved:true}.

   Demo note: the Prava iframe is shown for the passkey MOMENT; the run's
   approval gate is resolved by the POST. If the iframe URL can't render (sandbox
   blocks embedding), a clear fallback with the live link is shown so the moment
   still reads as "authorise on Prava". */

export default function ApprovalPanel({
  approval,
  onApprove,
  approving,
}: {
  approval: ApprovalRequest;
  onApprove: () => void;
  approving: boolean;
}) {
  const [frameFailed, setFrameFailed] = useState(false);
  const { cart, session, context } = approval;
  const merchant = context.approved_merchants[0];

  return (
    <div className={s.wrap}>
      <span className={s.badge}>
        <span className={s.badgeDot} />
        Human approval required
      </span>

      <h2 className={s.headline}>Authorise this spend</h2>
      <p className={s.lede}>
        The agent has a card session pinned to this exact merchant and amount.
        Confirm the passkey, then approve — nothing is charged until you do.
      </p>

      <div className={s.figure}>
        <span className={s.amount}>{money(cart.total_cents)}</span>
        <span className={s.to}>
          to <span className={s.toName}>{merchant?.name ?? "merchant"}</span>
          {" · "}
          {cart.items.length} item{cart.items.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className={s.frameLabel}>
        <LockGlyph />
        Prava · passkey verification
      </div>

      {!frameFailed ? (
        <div className={s.frameStage}>
          {/* Labelled substrate behind the frame — visible until (or if) the
              cross-origin Prava page paints, so this is never a blank void. */}
          <div className={s.frameSubstrate} aria-hidden="true">
            <span className={s.substrateRing}>
              <LockGlyph />
            </span>
            <span className={s.substrateTitle}>Prava secure session</span>
            <span className={s.substrateBody}>
              Card entry and passkey happen inside Prava&apos;s hosted frame,
              pinned to this merchant and amount.
            </span>
          </div>
          <iframe
            className={s.frame}
            src={session.iframe_url}
            title="Prava passkey verification"
            onError={() => setFrameFailed(true)}
            allow="publickey-credentials-get *; payment *"
            sandbox="allow-scripts allow-forms allow-same-origin allow-popups"
          />
        </div>
      ) : (
        <div className={s.frameFallback}>
          <div className={s.fbHead}>
            <LockGlyph />
            Verify on Prava
          </div>
          <div className={s.fbBody}>
            The passkey step opens in Prava&apos;s secure session for this
            purchase.
          </div>
          <a
            className={s.fbLink}
            href={session.iframe_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {session.iframe_url}
          </a>
        </div>
      )}

      <div className={s.actions}>
        <button
          className={s.approve}
          onClick={onApprove}
          disabled={approving}
        >
          {approving ? (
            <>
              <span className={s.spinner} />
              Approving…
            </>
          ) : (
            <>
              <ShieldCheck />
              Approve {money(cart.total_cents)}
            </>
          )}
        </button>
        <button className={s.decline} disabled={approving} type="button">
          Not now
        </button>
        <span className={s.note}>Session {session.session_id.slice(0, 14)}…</span>
      </div>
    </div>
  );
}

function LockGlyph() {
  return (
    <svg className={s.lock} width="14" height="14" viewBox="0 0 16 16" fill="none">
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
