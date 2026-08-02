"use client";

/* ApprovalCard — the one interactive tool card, and the emotional peak.

   It reuses the existing ApprovalPanel unchanged (it already implements the
   typed ToolCardProps<ApprovalRequest, ApprovalResult> contract: renders the
   cart total + merchant, mounts the Prava passkey iframe, and routes BOTH
   Approve and "Not now" through the single `resolve` callback, which POSTs
   /approve { approved, reason? } via useErrandRun.resolveApproval). This adapter
   only derives the pending → resolving → resolved status from the run phase and
   adds the shared card ENTER motion, so it sits in the thread like every other
   tool card but stays the standout, interactive moment.

   Content is fully present on mount; motion only decorates arrival. */

import { useReducedMotion, motion } from "motion/react";
import type { ApprovalRequest, ApprovalResult } from "@/lib/types";
import type { RunState } from "@/lib/useErrandRun";
import ApprovalPanel from "../stages/ApprovalPanel";

export default function ApprovalCard({
  approval,
  state,
  onResolve,
}: {
  approval: ApprovalRequest;
  state: RunState;
  onResolve: (r: ApprovalResult) => void;
}) {
  const reduce = useReducedMotion();

  const status =
    state.phase === "approving"
      ? "resolving"
      : state.phase === "declined" || state.approvalResult?.approved === false
        ? "resolved"
        : state.phase === "awaiting_approval"
          ? "pending"
          : // approved & moved on (working/done) — the panel reads resolved
            "resolved";

  // Once the spend is approved and the run has moved on, the live approval card
  // has done its job; the working/checkout cards carry the story from here. We
  // only keep it mounted while awaiting, approving, or resolved-declined.
  const approvedAndMovedOn =
    state.approvalResult?.approved === true &&
    state.phase !== "awaiting_approval" &&
    state.phase !== "approving";
  if (approvedAndMovedOn) return null;

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.19, ease: [0.22, 0.8, 0.28, 1] }}
    >
      <ApprovalPanel
        args={approval}
        result={state.approvalResult}
        status={status}
        resolve={onResolve}
      />
    </motion.div>
  );
}
