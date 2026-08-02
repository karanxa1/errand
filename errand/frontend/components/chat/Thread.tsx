"use client";

/* Thread — the conversational transcript. Renders the user's intent bubble, then
   walks the ORDERED audit stream (the same events lib/useErrandRun already
   parsed) and maps each to an animated tool-call card, a quiet status line, or a
   skip. The interactive approval card is injected in sequence. A shimmer
   "working…" card trails the live run so the thread always feels alive, and a
   conversational closing bubble lands on a terminal state.

   Every branch is total: unmapped/new events fall through to a generic card, so
   the thread NEVER crashes and NEVER renders an empty void. */

import type {
  ApprovalRequest,
  AuditEntry,
  CartResult,
  PurchaseContext,
} from "@/lib/types";
import type { RunState } from "@/lib/useErrandRun";
import { money } from "@/lib/format";

import type { WebSearchPayload } from "@/lib/errandReducer";
import ToolCard from "./ToolCard";
import {
  PolicyBody,
  CartBody,
  KeyValueBody,
  WebSearchBody,
  AgentBubble,
  ClosingBubble,
} from "./bodies";
import {
  PolicyIcon,
  CartIcon,
  LockIcon,
  CardIcon,
  BagIcon,
  MailIcon,
  InboxIcon,
  NodeIcon,
  SearchIcon,
  AlertIcon,
  CheckIcon,
} from "./ToolIcons";
import ApprovalCard from "./ApprovalCard";
import StatusLine from "./StatusLine";
import t from "./Thread.module.css";

// Events that carry no card of their own — the surrounding cards/bubble tell the
// story, so rendering them again would be noise.
const SILENT = new Set([
  "run.started",
  // terminal events → handled by the closing bubble
  "run.done",
  "run.error",
  // approval lifecycle → owned by the interactive ApprovalCard
  "approval.request",
  "approval.denied",
  // tool bridge bookkeeping → the tool's own card carries it (web_search card,
  // errand cards). tool.call for run_errand and every tool.result stay quiet.
  "tool.call",
  "tool.result",
  // voice errors surface on the page banner + orb, not as a thread card
  "voice.error",
  // connection blips → owned by the orb + the lost banner on the page
  "stream.reconnecting",
  "stream.connection_lost",
  "stream.error",
]);

interface Props {
  // The typed intent (text/SSE path) shown as the leading user bubble. In voice
  // mode this is empty — every turn arrives as a user.message audit entry.
  intent?: string;
  state: RunState;
  phaseLabel: string;
  onResolveApproval: (r: { approved: boolean; reason?: string }) => void;
  // The in-flight (not-yet-final) spoken utterance, shown as a forming bubble.
  interim?: string;
}

export default function Thread({
  intent,
  state,
  phaseLabel,
  onResolveApproval,
  interim,
}: Props) {
  const running =
    state.phase !== "done" &&
    state.phase !== "error" &&
    state.phase !== "declined";

  // The working shimmer only shows while an errand step is genuinely in flight
  // (not while idle chit-chat, not while waiting on the human, not on a terminal
  // state, and only with a live stream). idle = voice conversation between runs.
  const showWorking =
    running &&
    state.phase !== "idle" &&
    state.phase !== "awaiting_approval" &&
    (state.connection === "open" || state.connection === "connecting");

  const terminal =
    state.phase === "done" ||
    state.phase === "error" ||
    state.phase === "declined";

  const forming = (interim ?? "").trim();

  return (
    <div className={t.thread}>
      {/* Leading user turn (text path). Voice turns arrive in the audit stream. */}
      {intent ? (
        <div className={t.userRow}>
          <div className={t.userBubble}>{intent}</div>
        </div>
      ) : null}

      {/* Assistant turn — one item per event, in arrival order. User/agent turns
          are interleaved for the voice path. */}
      <div className={t.assistant}>
        {state.audit.map((e) => renderEntry(e, state, onResolveApproval))}

        {forming && (
          <div className={t.userRow}>
            <div className={`${t.userBubble} ${t.userForming}`}>{forming}</div>
          </div>
        )}

        {showWorking && (
          <ToolCard
            icon={<NodeIcon size={18} />}
            title={phaseLabel}
            meta="Agent is working…"
            status="running"
          />
        )}

        {terminal && <ClosingBubble state={state} />}
      </div>
    </div>
  );
}

function renderEntry(
  e: AuditEntry,
  state: RunState,
  onResolveApproval: (r: { approved: boolean; reason?: string }) => void,
) {
  const key = e.id;
  const p = (e.payload ?? {}) as Record<string, unknown>;

  // The interactive approval card is injected where its request landed.
  if (e.step === "approval.request") {
    if (!state.approval) return null;
    return (
      <ApprovalCard
        key={key}
        approval={state.approval as ApprovalRequest}
        state={state}
        onResolve={onResolveApproval}
      />
    );
  }

  // A spoken user turn (voice) — a right-aligned bubble in the thread.
  if (e.step === "user.message") {
    const text = (p.text as string) ?? e.detail;
    if (!text) return null;
    return (
      <div key={key} className={t.userRow}>
        <div className={t.userBubble}>{text}</div>
      </div>
    );
  }

  // An agent spoken reply (voice) — a calm left-aligned bubble.
  if (e.step === "agent.message") {
    const text = (p.text as string) ?? e.detail;
    if (!text) return null;
    return <AgentBubble key={key} text={text} />;
  }

  // A web search — a distinct card: query header, grounded answer, source chips.
  // Running (pending) until websearch.result fills it in place.
  if (e.step === "web_search") {
    const ws = p as unknown as WebSearchPayload;
    const query = ws.query || e.detail || "Web search";
    return (
      <ToolCard
        key={key}
        icon={<SearchIcon size={18} />}
        title="Searched the web"
        meta={query}
        status={ws.resolved ? "done" : "running"}
      >
        {ws.resolved ? (
          <WebSearchBody answer={ws.answer} sources={ws.sources ?? []} />
        ) : undefined}
      </ToolCard>
    );
  }

  if (SILENT.has(e.step)) return null;

  switch (e.step) {
    case "inbox.ready":
      return (
        <StatusLine
          key={key}
          icon={<InboxIcon size={15} />}
          text={
            state.inboxAddress
              ? `Agent inbox ready · ${state.inboxAddress}`
              : "Agent inbox ready"
          }
        />
      );

    case "context.loaded": {
      const ctx = p as unknown as PurchaseContext;
      const merchant = ctx.approved_merchants?.[0];
      return (
        <ToolCard
          key={key}
          icon={<PolicyIcon size={18} />}
          title="Consulted policy"
          meta={
            merchant
              ? `Senso · ${merchant.name} · ${money(ctx.budget_cents)} cap`
              : `Senso · ${money(ctx.budget_cents)} cap`
          }
          status="done"
        >
          <PolicyBody context={ctx} />
        </ToolCard>
      );
    }

    case "context.no_merchant":
      return (
        <ToolCard
          key={key}
          icon={<AlertIcon size={18} />}
          title="No approved merchant"
          meta={e.detail}
          status="error"
          tone="error"
        />
      );

    case "cart.built": {
      const cart = p as unknown as CartResult;
      return (
        <ToolCard
          key={key}
          icon={<CartIcon size={18} />}
          title="Built the cart"
          meta={`${cart.items.length} item${
            cart.items.length === 1 ? "" : "s"
          } · ${money(cart.total_cents)}`}
          status="done"
        >
          <CartBody cart={cart} budgetCents={state.context?.budget_cents} />
        </ToolCard>
      );
    }

    case "cart.over_budget":
      return (
        <ToolCard
          key={key}
          icon={<AlertIcon size={18} />}
          title="Cart exceeds budget"
          meta={e.detail || "Stopping before any spend."}
          status="error"
          tone="warn"
        />
      );

    case "payment.session":
      return (
        <ToolCard
          key={key}
          icon={<LockIcon size={18} />}
          title="Opened a secure payment session"
          meta="Prava · pinned to this merchant & amount"
          status="done"
        >
          <KeyValueBody
            rows={[
              {
                key: "Session",
                val: String(p.session_id ?? "—"),
                mono: true,
              },
              { key: "Provider", val: "Prava (hosted, passkey)" },
            ]}
          />
        </ToolCard>
      );

    case "approval.granted":
      return (
        <StatusLine
          key={key}
          icon={<CheckIcon size={14} />}
          tone="ok"
          text="You approved the spend (passkey confirmed)"
        />
      );

    case "approval.timeout":
      return (
        <ToolCard
          key={key}
          icon={<AlertIcon size={18} />}
          title="Approval timed out"
          meta={e.detail || "No decision in time — nothing was charged."}
          status="error"
          tone="warn"
        />
      );

    case "payment.credential":
      return (
        <ToolCard
          key={key}
          icon={<CardIcon size={18} />}
          title="Issued a one-time card"
          meta={
            state.credentialLast4
              ? `Single-use · ending ${state.credentialLast4}`
              : "Single-use credential"
          }
          status="done"
        >
          <KeyValueBody
            rows={[
              {
                key: "Card",
                val: state.credentialLast4
                  ? `•••• •••• •••• ${state.credentialLast4}`
                  : "issued",
                mono: true,
              },
              { key: "Scope", val: "One-time, this merchant only" },
            ]}
          />
        </ToolCard>
      );

    case "payment.failed":
    case "payment.timeout":
      return (
        <ToolCard
          key={key}
          icon={<AlertIcon size={18} />}
          title="Payment couldn't complete"
          meta={e.detail || "The credential wasn't issued."}
          status="error"
          tone="error"
        />
      );

    case "checkout.completed":
      return (
        <ToolCard
          key={key}
          icon={<BagIcon size={18} />}
          title="Placed the order"
          meta={state.orderId ? `Order ${state.orderId}` : e.detail}
          status="done"
        >
          <KeyValueBody
            rows={[
              { key: "Order", val: state.orderId ?? "placed", mono: true },
              ...(e.detail ? [{ key: "Merchant said", val: e.detail }] : []),
            ]}
          />
        </ToolCard>
      );

    case "payment.reported":
      return (
        <StatusLine
          key={key}
          icon={<CheckIcon size={14} />}
          tone="ok"
          text={e.detail || "Outcome reported to Prava"}
        />
      );

    case "payment.report_failed":
      return (
        <ToolCard
          key={key}
          icon={<AlertIcon size={18} />}
          title="Couldn't report the outcome to Prava"
          meta={e.detail}
          status="error"
          tone="warn"
        />
      );

    case "payment.declined":
      return (
        <ToolCard
          key={key}
          icon={<AlertIcon size={18} />}
          title="Checkout declined"
          meta={e.detail || "Reported DECLINED to Prava; nothing settled."}
          status="error"
          tone="error"
        />
      );

    case "mail.confirmation": {
      const matched = (p as { matched?: boolean }).matched;
      return (
        <ToolCard
          key={key}
          icon={<MailIcon size={18} />}
          title={
            matched === false
              ? "Watched the inbox for a receipt"
              : "Confirmation email received"
          }
          meta={
            state.confirmationOrderId
              ? `From agent inbox · ${state.confirmationOrderId}`
              : "From the agent inbox"
          }
          status="done"
        >
          <KeyValueBody
            rows={[
              {
                key: "Order",
                val: state.confirmationOrderId ?? "—",
                mono: true,
              },
              ...(state.inboxAddress
                ? [{ key: "Inbox", val: state.inboxAddress, mono: true }]
                : []),
            ]}
          />
        </ToolCard>
      );
    }

    case "run.aborted":
      return (
        <StatusLine
          key={key}
          icon={<AlertIcon size={14} />}
          tone="warn"
          text={e.detail || "Run aborted"}
        />
      );

    // Unknown / newly-added event: never crash — render a readable generic card
    // from the event name + detail, with its payload available for audit.
    default:
      return (
        <ToolCard
          key={key}
          icon={<NodeIcon size={18} />}
          title={humanize(e.step)}
          meta={e.detail || undefined}
          status="done"
        />
      );
  }
}

function humanize(step: string): string {
  const s = step.replace(/[._]/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
