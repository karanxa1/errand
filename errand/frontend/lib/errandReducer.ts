// errandReducer — the ONE pure event→state reducer, shared by both transports.
//
// The text composer runs an errand over SSE (lib/useErrandRun + lib/stream);
// the VoiceOrb runs one — or a whole conversation of them — over the voice WS
// (lib/useVoiceAgent). Both funnel their frames through `applyFrame` here, so a
// voice-driven run produces the EXACT same audit stream, and therefore the exact
// same animated tool cards in the chat thread, as a typed run. There is one
// render path; this file is why.
//
// Two wire shapes exist (see lib/stream.ts + backend app/voice/relay.py):
//   * audit-wrapped events  -> { at, step, detail, data }   payload under .data
//   * raw events            -> fields at the top level      (run.started,
//                                                            approval.request,
//                                                            run.done, run.error)
// plus the voice superset: tool.call, tool.result, websearch.result, and the
// synthesized user.message / agent.message turns.

import type {
  ApprovalRequest,
  ApprovalResult,
  AuditEntry,
  CartResult,
  PurchaseContext,
  RawFrame,
  RunDone,
} from "./types";

export type RunPhase =
  | "idle"
  | "starting"
  | "planning"
  | "cart"
  | "awaiting_approval"
  | "approving"
  | "working"
  | "done"
  | "declined"
  | "error";

// Live-stream connection health, tracked separately from the run phase so a
// network blip never destroys the run's own state.
export type ConnectionStatus =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "lost";

export interface RunState {
  phase: RunPhase;
  connection: ConnectionStatus;
  runId: string | null;
  model: string | null;
  inboxAddress: string | null;
  context: PurchaseContext | null;
  cart: CartResult | null;
  approval: ApprovalRequest | null;
  approvalResult: ApprovalResult | null;
  credentialLast4: string | null;
  orderId: string | null;
  confirmationOrderId: string | null;
  result: RunDone | null;
  errorMessage: string | null;
  audit: AuditEntry[];
}

export const initialRunState: RunState = {
  phase: "idle",
  connection: "idle",
  runId: null,
  model: null,
  inboxAddress: null,
  context: null,
  cart: null,
  approval: null,
  approvalResult: null,
  credentialLast4: null,
  orderId: null,
  confirmationOrderId: null,
  result: null,
  errorMessage: null,
  audit: [],
};

// A resolved/pending web-search card lives as a single audit entry whose payload
// carries the query, the grounded answer, its sources, and a resolved flag. The
// tool.call opens it (pending); websearch.result fills it in place.
export interface WebSearchPayload {
  query: string;
  answer: string | null;
  sources: { name: string; url: string; snippet?: string; favicon?: string }[];
  resolved: boolean;
}

// Monotonic audit id derived from the list itself, so no external counter is
// needed and a reset (empty audit) restarts cleanly.
function nextId(audit: AuditEntry[]): number {
  return audit.length === 0 ? 0 : audit[audit.length - 1].id + 1;
}

// Append a raw audit entry. Exported so the hooks can record their own transport
// notes (stream.reconnecting, connection lost, etc.) on the same timeline.
export function pushAuditEntry(
  state: RunState,
  at: string,
  step: string,
  detail: string,
  payload: unknown,
): RunState {
  return {
    ...state,
    audit: [
      ...state.audit,
      {
        id: nextId(state.audit),
        at,
        step,
        detail,
        payload: (payload as Record<string, unknown>) ?? null,
      },
    ],
  };
}

// The core: fold one decoded wire frame into the run state (audit + phase +
// fields). Total over every event — unknown/new events still land as a generic
// audit entry, so the thread never crashes and never renders an empty void.
export function applyFrame(state: RunState, frame: RawFrame): RunState {
  const event = frame.event;
  const data = (frame.data ?? {}) as Record<string, unknown>;

  // ── websearch.result: fill the pending web-search card IN PLACE (no new
  // card), so the search animates from running → resolved like one unit. ──────
  if (event === "websearch.result") {
    const audit = [...state.audit];
    const filled: WebSearchPayload = {
      query: (data.query as string) ?? "",
      answer: (data.answer as string) ?? "",
      sources:
        (data.sources as WebSearchPayload["sources"] | undefined) ?? [],
      resolved: true,
    };
    for (let i = audit.length - 1; i >= 0; i--) {
      const p = audit[i].payload as unknown as WebSearchPayload | null;
      if (audit[i].step === "web_search" && p && p.resolved === false) {
        audit[i] = {
          ...audit[i],
          payload: {
            ...filled,
            query: filled.query || p.query,
          } as unknown as Record<string, unknown>,
        };
        return { ...state, audit };
      }
    }
    // A result with no preceding call — record it resolved.
    return pushAuditEntry(state, new Date().toISOString(), "web_search", "Web search", filled);
  }

  // ── tool.call: web_search opens a pending card; run_errand is recorded but
  // rendered silently (the errand's own cards tell that story). ───────────────
  if (event === "tool.call") {
    const name = (data.name as string) ?? "";
    const args = (data.args as Record<string, unknown>) ?? {};
    if (name === "web_search") {
      const pending: WebSearchPayload = {
        query: (args.query as string) ?? "",
        answer: null,
        sources: [],
        resolved: false,
      };
      return pushAuditEntry(
        state,
        new Date().toISOString(),
        "web_search",
        "Web search",
        pending,
      );
    }
    return pushAuditEntry(
      state,
      new Date().toISOString(),
      "tool.call",
      typeof args.intent === "string" ? (args.intent as string) : name,
      { name, args },
    );
  }

  // tool.result is silent in the thread (the tool's own card/bubble carries the
  // outcome) but stays on the audit timeline for the raw log.
  if (event === "tool.result") {
    return pushAuditEntry(
      state,
      new Date().toISOString(),
      "tool.result",
      (data.summary as string) ?? "",
      data,
    );
  }

  // Synthesized conversation turns (voice): a user utterance / an agent reply.
  if (event === "user.message") {
    return pushAuditEntry(
      state,
      new Date().toISOString(),
      "user.message",
      (data.text as string) ?? "",
      data,
    );
  }
  if (event === "agent.message") {
    return pushAuditEntry(
      state,
      new Date().toISOString(),
      "agent.message",
      (data.text as string) ?? "",
      data,
    );
  }

  // ── errand events (shared with SSE) ─────────────────────────────────────────
  const isWrapped = typeof data.step === "string" && "detail" in data;
  const at =
    (isWrapped && typeof data.at === "string" && data.at) ||
    new Date().toISOString();
  const detail =
    (isWrapped && typeof data.detail === "string" && data.detail) || "";
  const payload = isWrapped ? (data.data ?? null) : data;

  let next = pushAuditEntry(state, at, event, detail || humanize(event), payload);

  switch (event) {
    case "run.started": {
      // A new run inside a voice conversation resets the run-scoped fields but
      // keeps the audit timeline, so earlier turns stay in the thread.
      next = {
        ...next,
        phase: "planning",
        runId: (data.run_id as string) ?? next.runId,
        model: (data.model as string) ?? next.model,
        context: null,
        cart: null,
        approval: null,
        approvalResult: null,
        credentialLast4: null,
        orderId: null,
        confirmationOrderId: null,
        result: null,
        errorMessage: null,
      };
      break;
    }
    case "inbox.ready":
      next = {
        ...next,
        inboxAddress:
          ((payload as { address?: string })?.address as string) ??
          next.inboxAddress,
      };
      break;
    case "context.loaded":
      next = {
        ...next,
        phase: "planning",
        context: payload as unknown as PurchaseContext,
      };
      break;
    case "cart.built":
      next = { ...next, phase: "cart", cart: payload as unknown as CartResult };
      break;
    case "approval.request":
      next = {
        ...next,
        phase: "awaiting_approval",
        approval: data as unknown as ApprovalRequest,
      };
      break;
    case "approval.granted":
      next = { ...next, phase: "working" };
      break;
    case "approval.denied":
      next = {
        ...next,
        phase: "declined",
        approvalResult: next.approvalResult ?? {
          approved: false,
          reason: ((payload as { reason?: string })?.reason as string) ?? undefined,
        },
      };
      break;
    case "approval.timeout":
      next = {
        ...next,
        approvalResult: next.approvalResult ?? { approved: false },
      };
      break;
    case "payment.credential":
      next = {
        ...next,
        phase: "working",
        credentialLast4:
          ((payload as { last4?: string })?.last4 as string) ?? next.credentialLast4,
      };
      break;
    case "checkout.completed":
      next = {
        ...next,
        orderId:
          ((payload as { order_id?: string })?.order_id as string) ?? next.orderId,
      };
      break;
    case "mail.confirmation":
      next = {
        ...next,
        confirmationOrderId:
          ((payload as { order_id?: string })?.order_id as string) ??
          next.confirmationOrderId,
      };
      break;
    case "run.done": {
      const done = data as unknown as RunDone;
      const completed = done.kind === "completed";
      const declined =
        next.phase === "declined" || next.approvalResult?.approved === false;
      const phase: RunPhase = completed ? "done" : declined ? "declined" : "error";
      next = {
        ...next,
        phase,
        result: done,
        orderId: done.order_id ?? next.orderId,
        confirmationOrderId:
          done.confirmation_order_id ?? next.confirmationOrderId,
        errorMessage:
          completed || declined ? null : done.reason ?? "Run did not complete.",
      };
      break;
    }
    case "run.error":
      next = {
        ...next,
        phase: "error",
        errorMessage: (data.message as string) ?? "The run failed.",
      };
      break;
    default:
      break;
  }

  return next;
}

export function humanize(event: string): string {
  const s = event.replace(/[._]/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
