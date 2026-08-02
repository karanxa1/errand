"use client";

import type { RunState } from "@/lib/useErrandRun";
import { money } from "@/lib/format";
import s from "./DonePanel.module.css";

export default function DonePanel({ state }: { state: RunState }) {
  if (state.phase === "error") {
    return (
      <div className={s.errWrap}>
        <div className={s.errHead}>
          <WarnGlyph />
          Run didn&apos;t complete
        </div>
        <div className={s.errBody}>
          {state.errorMessage || "The errand stopped before finishing."}
        </div>
      </div>
    );
  }

  const total = state.result?.total_cents ?? state.cart?.total_cents;

  return (
    <div className={s.wrap}>
      <span className={s.seal}>
        <span className={s.sealRing}>
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
            <path
              d="M3 8.5l3 3 7-7"
              stroke="currentColor"
              strokeWidth="1.9"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        Order placed
      </span>

      <h2 className={s.headline}>Done — and every step is on the record.</h2>

      <div className={s.grid}>
        <div className={s.stat}>
          <div className={s.statLabel}>Order</div>
          <div className={s.statValue}>{state.orderId ?? "—"}</div>
        </div>
        <div className={s.stat}>
          <div className={s.statLabel}>Paid</div>
          <div className={s.statValueBig}>{money(total)}</div>
        </div>
        <div className={s.stat}>
          <div className={s.statLabel}>Card used</div>
          <div className={s.statValue}>
            {state.credentialLast4 ? `•••• ${state.credentialLast4}` : "—"}
          </div>
        </div>
        {state.confirmationOrderId && (
          <div className={s.stat}>
            <div className={s.statLabel}>Email confirms</div>
            <div className={s.statValue}>{state.confirmationOrderId}</div>
          </div>
        )}
      </div>

      {state.inboxAddress && (
        <div className={s.inbox}>
          Confirmation caught at
          <span className={s.inboxAddr}>{state.inboxAddress}</span>
        </div>
      )}
    </div>
  );
}

function WarnGlyph() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path
        d="M9 2.5l6.5 11.5H2.5L9 2.5Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M9 7v3.2" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <circle cx="9" cy="12.3" r="0.9" fill="currentColor" />
    </svg>
  );
}
