"use client";

import type { RunState } from "@/lib/useErrandRun";
import { money } from "@/lib/format";

const STAT =
  "bg-ink-050 rounded-card px-4 py-[14px] shadow-[inset_0_0_0_1px_var(--color-edge)]";
const STAT_LABEL =
  "text-[11px] tracking-[0.08em] uppercase text-low mb-1.5";
const STAT_VALUE = "font-mono text-[17px] text-hi";

export default function DonePanel({ state }: { state: RunState }) {
  if (state.phase === "error") {
    return (
      <div className="bg-[image:linear-gradient(180deg,var(--color-ink-150),var(--color-ink-100))] rounded-panel shadow-[inset_0_1px_0_rgba(255,122,107,0.14),inset_0_0_0_1px_rgba(255,122,107,0.2)] p-6">
        <div className="flex items-center gap-2.5 text-danger font-semibold text-[15px] mb-2">
          <WarnGlyph />
          Run didn&apos;t complete
        </div>
        <div className="text-mid text-[13.5px] leading-[1.5]">
          {state.errorMessage || "The errand stopped before finishing."}
        </div>
      </div>
    );
  }

  const total = state.result?.total_cents ?? state.cart?.total_cents;

  return (
    <div className="bg-[image:radial-gradient(130%_100%_at_50%_-30%,rgba(19,239,147,0.1),transparent_62%),linear-gradient(180deg,var(--color-ink-150),var(--color-ink-100))] rounded-panel shadow-[inset_0_1px_0_rgba(160,240,200,0.12),inset_0_0_0_1px_var(--color-edge-strong)] px-6 py-[26px]">
      <span className="inline-flex items-center gap-[9px] text-green text-[12px] tracking-[0.1em] uppercase font-semibold mb-[14px]">
        <span className="w-[26px] h-[26px] rounded-full bg-green text-on-accent inline-flex items-center justify-center shadow-[inset_0_1px_0_rgba(255,255,255,0.25)]">
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

      <h2 className="font-display text-[32px] leading-[1.08] text-hi m-0 mb-[18px] tracking-[0.01em]">
        Done — and every step is on the record.
      </h2>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3 mb-2">
        <div className={STAT}>
          <div className={STAT_LABEL}>Order</div>
          <div className={STAT_VALUE}>{state.orderId ?? "—"}</div>
        </div>
        <div className={STAT}>
          <div className={STAT_LABEL}>Paid</div>
          <div className="font-display text-[26px] text-hi">{money(total)}</div>
        </div>
        <div className={STAT}>
          <div className={STAT_LABEL}>Card used</div>
          <div className={STAT_VALUE}>
            {state.credentialLast4 ? `•••• ${state.credentialLast4}` : "—"}
          </div>
        </div>
        {state.confirmationOrderId && (
          <div className={STAT}>
            <div className={STAT_LABEL}>Email confirms</div>
            <div className={STAT_VALUE}>{state.confirmationOrderId}</div>
          </div>
        )}
      </div>

      {state.inboxAddress && (
        <div className="mt-4 text-[12.5px] text-mid flex items-center gap-2">
          Confirmation caught at
          <span className="font-mono text-green-soft">
            {state.inboxAddress}
          </span>
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
