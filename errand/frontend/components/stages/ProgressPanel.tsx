"use client";

import type { RunState } from "@/lib/useErrandRun";
import "./stages.anim.css";

/* ProgressPanel — the working state after approval. Renders the payment →
   checkout → report → email sequence as a rail of nodes that fill in as the
   matching stream events arrive. Numbered/labelled steps with a rounded rail,
   not a bare hairline list. */

/* Shared stage-card language. */
const CARD =
  "bg-[image:linear-gradient(180deg,var(--color-ink-100),var(--color-ink-050))] rounded-card shadow-[inset_0_1px_0_rgba(160,240,200,0.06),inset_0_0_0_1px_var(--color-edge)] px-[22px] py-5";
const HEAD = "flex items-center gap-3 mb-4";
const STEP_NO =
  "font-mono text-[11px] text-green tracking-[0.06em] px-2 py-[3px] rounded-md bg-ink-200 shadow-[inset_0_0_0_1px_var(--color-edge)] flex-none";
const TITLE =
  "font-display text-[22px] text-hi m-0 tracking-[0.01em] leading-[1.1]";

const NODE_BASE =
  "w-5 h-5 rounded-full flex items-center justify-center flex-none transition-[background,box-shadow] duration-300 ease-[ease]";
const NODE_STATE = {
  done: "bg-green text-on-accent shadow-[inset_0_1px_0_rgba(255,255,255,0.2)]",
  active:
    "bg-ink-250 text-green shadow-[inset_0_0_0_1px_var(--color-edge-strong),0_0_0_4px_var(--color-green-glow)]",
  idle: "bg-ink-200 text-green shadow-[inset_0_0_0_1px_var(--color-edge)]",
} as const;

const LABEL_BASE =
  "text-[14px] [font-weight:550] transition-[color] duration-300 ease-[ease]";
const LABEL_STATE = {
  active: "text-hi",
  done: "text-body",
  pending: "text-low",
} as const;

interface Step {
  key: string;
  label: string;
  reached: (st: RunState) => boolean;
  detail?: (st: RunState) => string | undefined;
}

const STEPS: Step[] = [
  {
    key: "granted",
    label: "Spend approved (passkey)",
    reached: (st) =>
      st.audit.some((a) => a.step === "approval.granted") ||
      ["working", "done"].includes(st.phase),
  },
  {
    key: "credential",
    label: "One-time card credential issued",
    reached: (st) => st.credentialLast4 != null,
    detail: (st) =>
      st.credentialLast4 ? `card ending ${st.credentialLast4}` : undefined,
  },
  {
    key: "checkout",
    label: "Checkout completed at merchant",
    reached: (st) =>
      st.orderId != null ||
      st.audit.some((a) => a.step === "checkout.completed"),
    detail: (st) => (st.orderId ? st.orderId : undefined),
  },
  {
    key: "reported",
    label: "Outcome reported to Prava",
    reached: (st) => st.audit.some((a) => a.step === "payment.reported"),
  },
  {
    key: "mail",
    label: "Confirmation email caught",
    reached: (st) => st.audit.some((a) => a.step === "mail.confirmation"),
    detail: (st) =>
      st.confirmationOrderId ? st.confirmationOrderId : undefined,
  },
];

function NodeTick() {
  return (
    <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
      <path
        d="M2 6.2l2.4 2.4L10 3"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function ProgressPanel({ state }: { state: RunState }) {
  // Active step = first not-yet-reached step.
  const firstUnreached = STEPS.findIndex((st) => !st.reached(state));
  const activeIdx = state.phase === "done" ? STEPS.length : firstUnreached;

  return (
    <div className={CARD}>
      <div className={HEAD}>
        <span className={STEP_NO}>03 · SETTLE</span>
        <h2 className={TITLE}>Placing the order</h2>
      </div>

      <div className="grid gap-0">
        {STEPS.map((st, i) => {
          const done = st.reached(state);
          const active = i === activeIdx && state.phase !== "done";
          const detail = st.detail?.(state);
          const isLast = i === STEPS.length - 1;
          return (
            <div
              key={st.key}
              className="grid grid-cols-[30px_1fr] gap-[14px] py-1"
            >
              <div className="flex flex-col items-center">
                <div
                  className={`${NODE_BASE} ${
                    done
                      ? NODE_STATE.done
                      : active
                        ? NODE_STATE.active
                        : NODE_STATE.idle
                  }`}
                >
                  {done && <NodeTick />}
                </div>
                {!isLast && (
                  <div
                    className={`w-0.5 flex-1 min-h-[18px] rounded-[2px] my-0.5 ${
                      done ? "bg-green-dim" : "bg-ink-200"
                    }`}
                  />
                )}
              </div>
              <div className="pb-[14px]">
                <div
                  className={`${LABEL_BASE} ${
                    active
                      ? LABEL_STATE.active
                      : done
                        ? LABEL_STATE.done
                        : LABEL_STATE.pending
                  }`}
                >
                  {st.label}
                  {active && (
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-green ml-2 animate-[errand-pulse_1.1s_ease-in-out_infinite]" />
                  )}
                </div>
                {done && detail && (
                  <div className="text-[12px] text-mid mt-0.5 font-mono">
                    {detail}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
