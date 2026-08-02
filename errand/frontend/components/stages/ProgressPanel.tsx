"use client";

import type { RunState } from "@/lib/useErrandRun";
import head from "./stages.module.css";
import s from "./ProgressPanel.module.css";

/* ProgressPanel — the working state after approval. Renders the payment →
   checkout → report → email sequence as a rail of nodes that fill in as the
   matching stream events arrive. Numbered/labelled steps with a rounded rail,
   not a bare hairline list. */

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
    <div className={head.card}>
      <div className={head.head}>
        <span className={head.stepNo}>03 · SETTLE</span>
        <h2 className={head.title}>Placing the order</h2>
      </div>

      <div className={s.steps}>
        {STEPS.map((st, i) => {
          const done = st.reached(state);
          const active = i === activeIdx && state.phase !== "done";
          const detail = st.detail?.(state);
          const isLast = i === STEPS.length - 1;
          return (
            <div key={st.key} className={s.step}>
              <div className={s.rail}>
                <div
                  className={`${s.node} ${
                    done ? s.nodeDone : active ? s.nodeActive : ""
                  }`}
                >
                  {done && <NodeTick />}
                </div>
                {!isLast && (
                  <div className={`${s.line} ${done ? s.lineDone : ""}`} />
                )}
              </div>
              <div className={s.body}>
                <div
                  className={`${s.label} ${
                    active
                      ? s.labelActive
                      : done
                        ? ""
                        : s.labelPending
                  }`}
                >
                  {st.label}
                  {active && <span className={s.pulse} />}
                </div>
                {done && detail && <div className={s.detail}>{detail}</div>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
