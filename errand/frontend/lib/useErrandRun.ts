// useErrandRun — owns the state machine for one errand run and turns the raw SSE
// frames into typed UI state. Every frame ALSO becomes an audit entry, so the
// audit log renders every event with its detail, timestamped, in arrival order.
//
// Two frame shapes exist on the wire (verified against the live backend):
//   * audit events  -> { at, step, detail, data }   payload lives under .data
//   * raw events    -> fields at the top level        (run.started,
//                                                       approval.request,
//                                                       run.done, run.error)

"use client";

import { useCallback, useRef, useState } from "react";
import { api } from "./config";
import { startErrandStream, type RunStreamController } from "./stream";
import type {
  ApprovalRequest,
  AuditEntry,
  CartResult,
  PurchaseContext,
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
  | "error";

export interface RunState {
  phase: RunPhase;
  runId: string | null;
  model: string | null;
  inboxAddress: string | null;
  context: PurchaseContext | null;
  cart: CartResult | null;
  approval: ApprovalRequest | null;
  credentialLast4: string | null;
  orderId: string | null;
  confirmationOrderId: string | null;
  result: RunDone | null;
  errorMessage: string | null;
  audit: AuditEntry[];
}

const initialState: RunState = {
  phase: "idle",
  runId: null,
  model: null,
  inboxAddress: null,
  context: null,
  cart: null,
  approval: null,
  credentialLast4: null,
  orderId: null,
  confirmationOrderId: null,
  result: null,
  errorMessage: null,
  audit: [],
};

interface StartArgs {
  profile: string;
  intent: string;
  model: string;
  userId?: string;
  userEmail?: string;
}

export function useErrandRun() {
  const [state, setState] = useState<RunState>(initialState);
  const ctrl = useRef<RunStreamController | null>(null);
  const auditSeq = useRef(0);

  const pushAudit = useCallback(
    (at: string, step: string, detail: string, payload: unknown) => {
      setState((s) => ({
        ...s,
        audit: [
          ...s.audit,
          {
            id: auditSeq.current++,
            at,
            step,
            detail,
            payload: (payload as Record<string, unknown>) ?? null,
          },
        ],
      }));
    },
    [],
  );

  const reset = useCallback(() => {
    ctrl.current?.abort();
    ctrl.current = null;
    auditSeq.current = 0;
    setState(initialState);
  }, []);

  const start = useCallback(
    ({ profile, intent, model, userId, userEmail }: StartArgs) => {
      ctrl.current?.abort();
      auditSeq.current = 0;
      setState({ ...initialState, phase: "starting" });

      ctrl.current = startErrandStream(
        api("/api/errand/stream"),
        {
          profile,
          intent,
          model,
          user_id: userId || "u_demo",
          user_email: userEmail || "operator@example.com",
        },
        {
          onFrame: (frame) => {
            const { event, data } = frame;

            // Audit-wrapped events: { at, step, detail, data }.
            const isWrapped =
              typeof data.step === "string" && "detail" in data;
            const at =
              (isWrapped && typeof data.at === "string" && data.at) ||
              new Date().toISOString();
            const detail =
              (isWrapped && typeof data.detail === "string" && data.detail) ||
              "";
            const payload = isWrapped ? (data.data ?? null) : data;

            pushAudit(at, event, detail || humanize(event), payload);

            switch (event) {
              case "run.started":
                setState((s) => ({
                  ...s,
                  phase: "planning",
                  runId: (data.run_id as string) ?? null,
                  model: (data.model as string) ?? null,
                }));
                break;
              case "inbox.ready":
                setState((s) => ({
                  ...s,
                  inboxAddress:
                    ((payload as { address?: string })?.address as string) ??
                    null,
                }));
                break;
              case "context.loaded":
                setState((s) => ({
                  ...s,
                  phase: "planning",
                  context: payload as unknown as PurchaseContext,
                }));
                break;
              case "cart.built":
                setState((s) => ({
                  ...s,
                  phase: "cart",
                  cart: payload as unknown as CartResult,
                }));
                break;
              case "approval.request":
                setState((s) => ({
                  ...s,
                  phase: "awaiting_approval",
                  approval: data as unknown as ApprovalRequest,
                }));
                break;
              case "approval.granted":
                setState((s) => ({ ...s, phase: "working" }));
                break;
              case "payment.credential":
                setState((s) => ({
                  ...s,
                  phase: "working",
                  credentialLast4:
                    ((payload as { last4?: string })?.last4 as string) ?? null,
                }));
                break;
              case "checkout.completed":
                setState((s) => ({
                  ...s,
                  orderId:
                    ((payload as { order_id?: string })?.order_id as string) ??
                    s.orderId,
                }));
                break;
              case "mail.confirmation":
                setState((s) => ({
                  ...s,
                  confirmationOrderId:
                    ((payload as { order_id?: string })
                      ?.order_id as string) ?? s.confirmationOrderId,
                }));
                break;
              case "run.done": {
                const done = data as unknown as RunDone;
                setState((s) => ({
                  ...s,
                  phase: done.kind === "completed" ? "done" : "error",
                  result: done,
                  orderId: done.order_id ?? s.orderId,
                  confirmationOrderId:
                    done.confirmation_order_id ?? s.confirmationOrderId,
                  errorMessage:
                    done.kind === "completed"
                      ? null
                      : done.reason ?? "Run did not complete.",
                }));
                break;
              }
              case "run.error":
                setState((s) => ({
                  ...s,
                  phase: "error",
                  errorMessage:
                    (data.message as string) ?? "The run failed.",
                }));
                break;
              default:
                break;
            }
          },
          onError: (message) => {
            pushAudit(new Date().toISOString(), "stream.error", message, {
              message,
            });
            setState((s) => ({ ...s, phase: "error", errorMessage: message }));
          },
        },
      );
    },
    [pushAudit],
  );

  // Approve the pending spend. The Prava passkey iframe is mounted in the UI for
  // the passkey moment; the approval gate itself is resolved by this POST.
  const approve = useCallback(async () => {
    const runId = state.approval?.run_id ?? state.runId;
    if (!runId) return;
    setState((s) => ({ ...s, phase: "approving" }));
    try {
      await fetch(api(`/api/errand/${runId}/approve`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: true }),
      });
      // Progress events continue arriving on the open SSE stream — we do NOT
      // poll for them.
    } catch (err) {
      setState((s) => ({
        ...s,
        phase: "error",
        errorMessage: `Approval failed to reach backend: ${
          (err as Error).message
        }`,
      }));
    }
  }, [state.approval?.run_id, state.runId]);

  return { state, start, approve, reset };
}

function humanize(event: string): string {
  return event.replace(/[._]/g, " ");
}
