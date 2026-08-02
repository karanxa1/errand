// SSE client for the errand run.
//
// NON-NEGOTIABLE streaming rule: the client CONSUMES the server's SSE stream and
// NEVER polls. `EventSource` can't issue a POST, so we open the stream with
// fetch(), read the ReadableStream, and parse `event:` / `data:` frames by hand.
//
// Frame grammar (text/event-stream): frames are separated by a blank line; each
// frame has an `event: <name>` line and one or more `data: <json>` lines.
//
// RESILIENCE (client-only; no server resume token exists yet):
//   A run is a POST SSE, so every re-POST would START A NEW RUN server-side and
//   could DOUBLE-CHARGE. We therefore split the failure surface in two:
//   * BEFORE any frame has arrived (the run hasn't started on the server): a
//     transient connect failure is safe to retry with bounded exponential
//     backoff, because no run exists yet. `onReconnecting` fires per attempt so
//     the UI (the orb) can show a "reconnecting" motion instead of a hard error.
//   * AFTER the first frame (a run IS live on the server): a drop is NOT
//     auto-retried — re-POSTing would spawn a duplicate run. Instead we surface
//     `onConnectionLost` so the UI can degrade gracefully: keep every event/audit
//     already received visible and offer a manual retry the operator controls.
//   Only a real terminal error (retries exhausted with zero frames) reaches
//   `onError`. A clean end after a terminal event (`run.done`/`run.error`) is
//   `onDone` and never triggers reconnect.

import type { RawFrame } from "./types";

export interface RunStreamHandlers {
  onFrame: (frame: RawFrame) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
  // A transient connect failure is being retried (no run started yet). `attempt`
  // is 1-based; `delayMs` is how long we wait before the next try.
  onReconnecting?: (attempt: number, delayMs: number) => void;
  // The live stream dropped mid-run. We do NOT auto-reconnect (would duplicate
  // the run); the UI should show a manual-retry affordance and keep what it has.
  onConnectionLost?: (message: string) => void;
}

export interface RunStreamOptions {
  profile: string;
  intent: string;
  model: string;
  // Stable per-browser identity, forwarded to Prava when the card session opens.
  // No user_id / user_email: the backend takes the spender's identity from the
  // verified bearer token and does not read them off the body, so sending them
  // only suggested the client got a say in who pays.
  browser_profile_id?: string;
}

export interface RunStreamController {
  abort: () => void;
}

// Bounded backoff: a small number of quick tries, capped, only for the
// pre-run-start window where retrying is safe.
const MAX_CONNECT_ATTEMPTS = 3;
const BACKOFF_BASE_MS = 500;
const BACKOFF_MAX_MS = 4000;

function backoffDelay(attempt: number): number {
  return Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** (attempt - 1));
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const id = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(id);
        resolve();
      },
      { once: true },
    );
  });
}

export function startErrandStream(
  url: string,
  options: RunStreamOptions,
  handlers: RunStreamHandlers,
): RunStreamController {
  const controller = new AbortController();

  (async () => {
    // These persist ACROSS reconnect attempts. Once we've seen a single frame,
    // a run is live on the server and re-POSTing is forbidden (double-charge).
    let receivedAnyFrame = false;
    let sawTerminal = false;
    let attempt = 0;

    // Consume one fetch+reader lifecycle. Returns "clean" if the body ended
    // normally, or throws on a network/read failure so the caller can decide
    // whether reconnecting is safe.
    async function consumeOnce(): Promise<"clean"> {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        // A non-2xx is a definite backend refusal, not a transient blip — but
        // only fatal if no run has started. If a run is already live this path
        // can't happen (we hold one open stream), so treat as connect failure.
        throw new Error(`Backend responded ${res.status}.`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Split complete frames on the blank-line delimiter (handles \n\n and
        // \r\n\r\n).
        let sep: number;
        while ((sep = indexOfFrameBoundary(buffer)) !== -1) {
          const rawFrame = buffer.slice(0, sep);
          buffer = buffer.slice(sep).replace(/^(\r?\n){2}/, "");
          const parsed = parseFrame(rawFrame);
          if (parsed) {
            receivedAnyFrame = true;
            if (parsed.event === "run.done" || parsed.event === "run.error") {
              sawTerminal = true;
            }
            handlers.onFrame(parsed);
          }
        }
      }
      return "clean";
    }

    while (true) {
      attempt += 1;
      try {
        await consumeOnce();
        // Body ended. If we reached a terminal event (or any frames at all with
        // a clean close), the run is over — signal done and stop.
        handlers.onDone?.();
        return;
      } catch (err) {
        if (controller.signal.aborted) return;

        const message = (err as Error).message || "Stream connection failed.";

        if (receivedAnyFrame && !sawTerminal) {
          // A run is LIVE on the server and the stream dropped mid-flight.
          // Re-POSTing would start a second run (double-charge), so we do NOT
          // auto-reconnect. Degrade gracefully: the UI keeps every received
          // event/audit and offers a manual retry the operator owns.
          handlers.onConnectionLost?.(
            "The live connection to the running errand dropped. " +
              "Everything received so far is preserved below.",
          );
          return;
        }

        if (sawTerminal) {
          // Terminal event already delivered; a trailing drop is harmless.
          handlers.onDone?.();
          return;
        }

        // No frame yet → no server-side run exists → retrying is safe.
        if (attempt >= MAX_CONNECT_ATTEMPTS) {
          handlers.onError?.(
            `Could not reach the agent backend after ${attempt} attempts. ${message}`,
          );
          return;
        }
        const delay = backoffDelay(attempt);
        handlers.onReconnecting?.(attempt, delay);
        await sleep(delay, controller.signal);
        if (controller.signal.aborted) return;
        // loop → try again
      }
    }
  })();

  return { abort: () => controller.abort() };
}

function indexOfFrameBoundary(buf: string): number {
  const a = buf.indexOf("\n\n");
  const b = buf.indexOf("\r\n\r\n");
  if (a === -1) return b;
  if (b === -1) return a;
  return Math.min(a, b);
}

function parseFrame(raw: string): RawFrame | null {
  const lines = raw.split(/\r?\n/);
  let event = "message";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }
  if (dataLines.length === 0) return null;
  try {
    const data = JSON.parse(dataLines.join("\n"));
    return { event, data };
  } catch {
    return null;
  }
}
