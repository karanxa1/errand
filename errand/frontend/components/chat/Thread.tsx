"use client";

/* Thread — the conversational transcript. Renders the user's intent bubble, then
   walks the ORDERED audit stream (the same events lib/useErrandRun already
   parsed) and maps each to an animated tool-call card, a quiet status line, or a
   skip. The interactive approval card is injected in sequence. A shimmer
   "working…" card trails the live run so the thread always feels alive, and a
   conversational closing bubble lands on a terminal state.

   Every branch is total: unmapped/new events fall through to a generic card, so
   the thread NEVER crashes and NEVER renders an empty void. */

import { useEffect, useRef, useState } from "react";
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

/* User turn — a right-aligned bubble, tonal (not the accent). Voice turns
   interleave user bubbles into the assistant column, where they get a little
   room to breathe against the cards around them (USER_ROW_INLINE). */
const USER_ROW = "flex justify-end";
const USER_ROW_INLINE = "my-1 flex justify-end";
const USER_BUBBLE =
  "max-w-[84%] rounded-[16px_16px_4px_16px] bg-[linear-gradient(180deg,var(--color-ink-200),var(--color-ink-150))] px-4 py-3 text-[14.5px] leading-[1.5] text-hi shadow-[inset_0_1px_0_rgba(160,240,200,0.08),inset_0_0_0_1px_var(--color-edge)]";
// The forming (interim) spoken utterance — a quieter, in-progress user bubble.
const USER_BUBBLE_FORMING =
  "max-w-[84%] rounded-[16px_16px_4px_16px] bg-[linear-gradient(180deg,var(--color-ink-150),var(--color-ink-100))] px-4 py-3 text-[14.5px] leading-[1.5] text-mid shadow-[inset_0_1px_0_rgba(160,240,200,0.05),inset_0_0_0_1px_var(--color-edge)]";

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
    <div className="flex w-full flex-col gap-[22px]">
      {/* Leading user turn (text path). Voice turns arrive in the audit stream. */}
      {intent ? (
        <div className={USER_ROW}>
          <div className={USER_BUBBLE}>{intent}</div>
        </div>
      ) : null}

      {/* Assistant turn — one item per event, in arrival order. User/agent turns
          are interleaved for the voice path. */}
      <div className="flex flex-col gap-3">
        {state.audit.map((e) => renderEntry(e, state, onResolveApproval))}

        {/* Live browser view — the agent shopping on screen. Shown while a frame
            exists and the run is still going; it holds as a still on a terminal
            state. A real <img> present by default (never opacity-gated), so a
            dropped animation can never blank it. */}
        {state.browserFrame && running && (
          <BrowserView
            src={state.browserFrame.src}
            caption={state.browserFrame.caption}
          />
        )}

        {/* Live browser handoff — the agent has passed the interactive browser
            back so the human can log in / pay. Shown while the run is still
            going. A real <iframe> present on mount (never opacity-gated), so a
            dropped animation can never blank it. */}
        {state.liveView && running && (
          <LiveViewCard url={state.liveView.url} onResolve={onResolveApproval} />
        )}

        {forming && (
          <div className={USER_ROW_INLINE}>
            <div className={USER_BUBBLE_FORMING}>{forming}</div>
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
      <div key={key} className={USER_ROW_INLINE}>
        <div className={USER_BUBBLE}>{text}</div>
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

  // Agentic shop-loop progress notes (both the demo store and the real wallet
  // path emit these). Quiet status lines, not full cards — the cart/approval
  // cards carry the substance; these are the running commentary beside them.
  if (e.step.startsWith("shop.")) {
    const tone =
      e.step === "shop.refused" || e.step === "shop.invalid" ? "warn" : "ok";
    return (
      <StatusLine
        key={key}
        icon={tone === "warn" ? <AlertIcon size={14} /> : <CartIcon size={14} />}
        tone={tone}
        text={e.detail || humanize(e.step)}
      />
    );
  }

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

/* The live browser view: a screenshot of the store the agent is shopping, with
   the current action as a caption and a quiet "live" marker. The image updates
   in place as new frames arrive (the reducer keeps only the latest), so this is
   one element re-sourced, not a growing list. Tonal card, self-coloured lip — no
   drawn border, no bloom. */
function BrowserView({ src, caption }: { src: string; caption: string }) {
  return (
    <figure className="m-0 overflow-hidden rounded-card bg-ink-050 shadow-[inset_0_0_0_1px_var(--color-edge)]">
      <figcaption className="flex items-center gap-2 px-[14px] py-[9px] text-[12px] text-mid shadow-[inset_0_-1px_0_var(--color-edge)]">
        <span
          className="h-[7px] w-[7px] flex-none rounded-full bg-green animate-[typingPulse_1.4s_ease-in-out_infinite]"
          aria-hidden="true"
        />
        <span className="[font-weight:600] text-hi">Live</span>
        <span className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
          {caption || "Shopping the store"}
        </span>
      </figcaption>
      {/* eslint-disable-next-line @next/next/no-img-element — a base64 data: URL
          streamed frame-by-frame is not something next/image can optimise. */}
      <img
        src={src}
        alt={caption || "The agent shopping the store"}
        className="block w-full"
      />
    </figure>
  );
}

// How long the interactive live-view frame gets before we call it blocked. An
// iframe fires no onError when the response is refused or third-party storage is
// withheld, so silence is the only signal — we surface an "open in a new tab"
// escape hatch after the wait. (Same discipline as ApprovalPanel's stall timer;
// written self-contained here rather than shared.)
const LIVE_VIEW_LOAD_TIMEOUT_MS = 12_000;

/* The live browser handoff: an INTERACTIVE frame of the Cloudflare live-view
   session (live.browser.run sends frame-ancestors *, so it embeds). The human
   logs in and pays here; the agent never sees their card. If the frame hasn't
   loaded in ~12s (blocked storage, a refused embed), a plain "open in a new tab"
   link takes over so the moment never dead-ends. Tonal card, self-coloured lip —
   no drawn border, no bloom. */
function LiveViewCard({
  url,
  onResolve,
}: {
  url: string;
  onResolve: (r: { approved: boolean; reason?: string }) => void;
}) {
  const [stalled, setStalled] = useState(false);
  // The human resolves the handoff exactly once — clicking Done or Cancel locks
  // the control so a double-tap can't fire two signals into the same run.
  const [resolved, setResolved] = useState(false);
  const loadTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    loadTimer.current = setTimeout(
      () => setStalled(true),
      LIVE_VIEW_LOAD_TIMEOUT_MS,
    );
    return () => {
      if (loadTimer.current) clearTimeout(loadTimer.current);
      loadTimer.current = null;
    };
  }, []);

  return (
    <figure className="m-0 overflow-hidden rounded-card bg-ink-050 shadow-[inset_0_0_0_1px_var(--color-edge)]">
      <figcaption className="flex flex-col gap-1 px-[14px] py-[11px] shadow-[inset_0_-1px_0_var(--color-edge)]">
        <span className="flex items-center gap-2 text-[13.5px] [font-weight:600] text-hi">
          <span
            className="h-[7px] w-[7px] flex-none rounded-full bg-green animate-[typingPulse_1.4s_ease-in-out_infinite]"
            aria-hidden="true"
          />
          Finish in the live browser
        </span>
        <span className="text-[12px] leading-[1.45] text-mid">
          Log in and pay here — the agent never sees your card.
        </span>
      </figcaption>

      {stalled && (
        <div className="px-[14px] py-2.5 text-[12px] leading-[1.5] text-body shadow-[inset_0_-1px_0_var(--color-edge)]">
          The live browser didn&apos;t load here.{" "}
          <a
            className="text-green-soft underline underline-offset-2"
            href={url}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open it in a new tab
          </a>{" "}
          instead.
        </div>
      )}

      <iframe
        src={url}
        title="Live browser handoff"
        className="block w-full h-[clamp(420px,70vh,760px)] border-none bg-ink-000"
        onLoad={() => {
          if (loadTimer.current) clearTimeout(loadTimer.current);
          loadTimer.current = null;
          setStalled(false);
        }}
        sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-storage-access-by-user-activation"
      />

      {/* The human's hand-back: 'Done paying' resolves the run, 'Cancel' aborts
          it. This is the client half of the handoff contract — without it the run
          waits out its whole budget. One decisive action + a quiet cancel, not a
          filled+outline pair. */}
      <div className="flex items-center gap-3 px-[14px] py-3 shadow-[inset_0_1px_0_var(--color-edge)]">
        <button
          type="button"
          disabled={resolved}
          onClick={() => {
            setResolved(true);
            onResolve({ approved: true });
          }}
          className="rounded-xl border-none bg-green px-[18px] py-2.5 text-[13.5px] [font-weight:660] text-on-accent shadow-[inset_0_1px_0_rgba(255,255,255,0.22)] transition-[background-color] duration-[160ms] ease-[ease] enabled:hover:bg-green-soft disabled:bg-ink-200 disabled:text-low"
        >
          {resolved ? "Thanks — wrapping up…" : "I've finished paying"}
        </button>
        <button
          type="button"
          disabled={resolved}
          onClick={() => {
            setResolved(true);
            onResolve({ approved: false });
          }}
          className="border-none bg-transparent px-1 py-1.5 text-[12.5px] text-low transition-[color] duration-[160ms] ease-[ease] enabled:hover:text-body disabled:text-low"
        >
          Cancel
        </button>
      </div>
    </figure>
  );
}
