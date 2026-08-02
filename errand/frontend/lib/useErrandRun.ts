// useErrandRun — owns the state machine for one errand run over SSE and turns the
// raw SSE frames into typed UI state via the SHARED reducer (lib/errandReducer),
// so the text path and the voice path produce identical chat-thread cards.
//
// Two frame shapes exist on the wire (verified against the live backend):
//   * audit events  -> { at, step, detail, data }   payload lives under .data
//   * raw events    -> fields at the top level        (run.started,
//                                                       approval.request,
//                                                       run.done, run.error)

"use client";

import { useCallback, useRef, useState } from "react";
import { api } from "./config";
import { getBrowserProfileId } from "./deviceProfile";
import { approvalFailure } from "./approvalError";
import { startErrandStream, type RunStreamController } from "./stream";
import {
  applyFrame,
  initialRunState,
  pushAuditEntry,
  type RunState,
} from "./errandReducer";
import type { ApprovalResult } from "./types";

// Re-export the shared state types so existing importers of useErrandRun keep
// working unchanged.
export type { RunPhase, ConnectionStatus, RunState } from "./errandReducer";

// No userId/userEmail here on purpose. /api/errand/stream derives both from the
// verified bearer token (app/main.py ErrandRequest carries neither field) —
// precisely so a caller cannot attribute a purchase to someone else. Sending
// them was dead weight that read as if the client chose who was spending.
interface StartArgs {
  profile: string;
  intent: string;
  model: string;
}

export function useErrandRun() {
  const [state, setState] = useState<RunState>(initialRunState);
  const ctrl = useRef<RunStreamController | null>(null);
  // Remember the last start args so a manual "retry" after a lost connection can
  // relaunch the same intent (a NEW run — retry is explicit and operator-owned).
  const lastArgs = useRef<StartArgs | null>(null);

  const reset = useCallback(() => {
    ctrl.current?.abort();
    ctrl.current = null;
    setState(initialRunState);
  }, []);

  const start = useCallback((args: StartArgs) => {
    const { profile, intent, model } = args;
    lastArgs.current = args;
    ctrl.current?.abort();
    setState({ ...initialRunState, phase: "starting", connection: "connecting" });

    ctrl.current = startErrandStream(
      api("/api/errand/stream"),
      {
        profile,
        intent,
        model,
        // Forwarded by the backend to Prava when it opens the card session. It
        // must be the SAME value on every checkout from this browser: a new one
        // reads as a new device and burns a device binding off the token.
        browser_profile_id: getBrowserProfileId(),
      },
      {
        onFrame: (frame) => {
          setState((s) => {
            // First frame proves the connection is live and open.
            const opened = s.connection === "open" ? s : { ...s, connection: "open" as const };
            return applyFrame(opened, frame);
          });
        },
        onError: (message) => {
          // Only reached when reconnect retries exhausted with zero frames —
          // i.e. no run ever started. A genuine hard failure.
          setState((s) => ({
            ...pushAuditEntry(s, new Date().toISOString(), "stream.error", message, {
              message,
            }),
            phase: "error",
            connection: "lost",
            errorMessage: message,
          }));
        },
        onReconnecting: (attempt, delayMs) => {
          setState((s) => ({
            ...pushAuditEntry(
              s,
              new Date().toISOString(),
              "stream.reconnecting",
              `Connection blip — retrying (attempt ${attempt}, in ${Math.round(
                delayMs,
              )}ms).`,
              { attempt, delayMs },
            ),
            connection: "reconnecting",
          }));
        },
        onConnectionLost: (message) => {
          // The live run dropped mid-flight. Keep the run phase (so received
          // panels/audit stay visible) and only flag the connection as lost.
          setState((s) => ({
            ...pushAuditEntry(
              s,
              new Date().toISOString(),
              "stream.connection_lost",
              message,
              { message },
            ),
            connection: "lost",
          }));
        },
        onDone: () => {
          setState((s) =>
            s.connection === "open" || s.connection === "connecting"
              ? { ...s, connection: "idle" }
              : s,
          );
        },
      },
    );
  }, []);

  // Manual retry after a lost connection. Relaunches the same intent as a NEW
  // run (there is no server resume token yet), which the operator triggers
  // explicitly — never automatic, so a live run is never silently duplicated.
  const retry = useCallback(() => {
    if (lastArgs.current) start(lastArgs.current);
  }, [start]);

  // Resolve the human-in-the-loop approval gate with a typed verdict. POSTs
  // { approved, reason? } to /approve; the run's terminal state then arrives on
  // the open SSE stream (approval.granted → working, or approval.denied →
  // declined). We never poll.
  const resolveApproval = useCallback(
    async (verdict: ApprovalResult) => {
      const runId = state.approval?.run_id ?? state.runId;
      if (!runId) return;
      setState((s) => ({
        ...s,
        approvalResult: verdict,
        phase: verdict.approved ? "approving" : s.phase,
      }));
      try {
        const res = await fetch(api(`/api/errand/${runId}/approve`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            approved: verdict.approved,
            ...(verdict.reason ? { reason: verdict.reason } : {}),
          }),
        });
        // The verdict reaching the backend and the backend ACCEPTING it are two
        // different things, and this used to ignore the second. "Expired code",
        // "binding limit reached", "card verification failed" and "device not
        // supported" all arrive here as distinct answers; collapsing them into a
        // generic failure (or into nothing at all, while the UI sits on
        // "approving") turns a ten-second recovery into a support ticket. So
        // surface exactly what the backend said.
        const failure = await approvalFailure(res);
        if (failure) {
          setState((s) => ({ ...s, phase: "error", errorMessage: failure }));
        }
      } catch (err) {
        setState((s) => ({
          ...s,
          phase: "error",
          connection: "lost",
          errorMessage: `Approval failed to reach backend: ${(err as Error).message}`,
        }));
      }
    },
    [state.approval?.run_id, state.runId],
  );

  const approve = useCallback(
    () => resolveApproval({ approved: true }),
    [resolveApproval],
  );

  return { state, start, approve, resolveApproval, retry, reset };
}
