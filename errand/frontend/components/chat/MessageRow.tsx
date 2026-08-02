"use client";

/* One committed history row — the memoized boundary that keeps streaming cheap.
 *
 * ChatView re-renders on every streaming token (chat.liveAssistant / chat.liveRun
 * change per token). Without a boundary, that re-runs the whole history .map on
 * every token and re-renders every already-committed row — and, for server-loaded
 * tool rows, re-derives runStateFromEvents(events) each time. None of that history
 * changes mid-turn.
 *
 * A committed message object has STABLE identity: server-loaded messages are set
 * once on load; locally-committed ones are built once in useChat.send's finally
 * block and never mutated. So the comparator below can gate a re-render on plain
 * reference/scalar equality of the message (id + role + content, plus the
 * runState/events references for tool rows) and the approval callback. When those
 * are unchanged we return true and React skips the row entirely — only the live
 * streaming bubble in ChatView keeps updating.
 *
 * (Comparator idea adapted from better-chatbot's PreviewMessage memo; the markup
 * and styling here are Errand's own.) */

import { memo, useMemo } from "react";

import { runStateFromEvents, type ChatMessage } from "@/lib/useChat";
import type { RunPhase } from "@/lib/errandReducer";
import type { ApprovalResult } from "@/lib/types";

import Thread from "./Thread";
import { AgentBubble } from "./bodies";

// The user-turn row + bubble. Exported so ChatView renders the live (not-yet-
// committed) user turn with the exact same treatment — one source of truth.
export const userRow = "flex justify-end";
export const userBubble =
  "max-w-[84%] px-4 py-3 rounded-[16px_16px_4px_16px] bg-[linear-gradient(180deg,var(--color-ink-200),var(--color-ink-150))] shadow-[inset_0_1px_0_rgba(160,240,200,0.08),inset_0_0_0_1px_var(--color-edge)] text-hi text-[14.5px] leading-[1.5]";

interface MessageRowProps {
  message: ChatMessage;
  // Stable across renders (useChat's resolveApproval is a useCallback([])), so it
  // never trips the comparator — but it is compared anyway to stay correct if that
  // ever changes.
  onResolveApproval: (verdict: ApprovalResult) => void;
}

function MessageRowInner({ message, onResolveApproval }: MessageRowProps) {
  if (message.role === "user") {
    return (
      <div className={userRow}>
        <div className={userBubble}>{message.content}</div>
      </div>
    );
  }

  if (message.role === "assistant") {
    return message.content.trim() ? <AgentBubble text={message.content} /> : null;
  }

  // tool: a locally-committed turn already carries the finished RunState; a
  // server-loaded one carries the frames to replay. Deriving from events is the
  // one non-trivial cost this memo protects, so it is keyed on the exact inputs.
  return (
    <ToolRow message={message} onResolveApproval={onResolveApproval} />
  );
}

// Split out so the events→RunState derivation can useMemo on the message's own
// (stable) references, keeping it off the render hot path.
function ToolRow({ message, onResolveApproval }: MessageRowProps) {
  const runState = useMemo(
    () => message.runState ?? runStateFromEvents(message.events),
    [message.runState, message.events],
  );
  if (runState.audit.length === 0) return null;
  return (
    <Thread
      state={runState}
      phaseLabel={phaseLabel(runState.phase)}
      onResolveApproval={onResolveApproval}
    />
  );
}

// Gate a re-render on what actually renders differently. A committed message is
// immutable, so unchanged id + role + content (and, for tool rows, the same
// runState/events references) plus the same approval callback means the row is
// pixel-identical: return true to skip it. Anything else → re-render.
function messagesEqual(prev: MessageRowProps, next: MessageRowProps): boolean {
  const a = prev.message;
  const b = next.message;
  if (a === b && prev.onResolveApproval === next.onResolveApproval) return true;
  if (a.id !== b.id) return false;
  if (a.role !== b.role) return false;
  if (a.content !== b.content) return false;
  if (a.runState !== b.runState) return false;
  if (a.events !== b.events) return false;
  if (prev.onResolveApproval !== next.onResolveApproval) return false;
  return true;
}

const MessageRow = memo(MessageRowInner, messagesEqual);
export default MessageRow;

// Shared with ChatView so the live turn's Thread labels match a committed one.
export function phaseLabel(phase: RunPhase | string): string {
  switch (phase) {
    case "starting":
      return "Starting the run";
    case "planning":
      return "Grounding against policy";
    case "cart":
      return "Building the cart";
    case "awaiting_approval":
      return "Waiting for your approval";
    case "approving":
      return "Confirming the passkey";
    case "working":
      return "Settling the order";
    case "done":
      return "Complete";
    case "declined":
      return "Declined";
    case "error":
      return "Stopped";
    default:
      return "Working";
  }
}
