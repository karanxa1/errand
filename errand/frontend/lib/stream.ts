// SSE client for the errand run.
//
// NON-NEGOTIABLE streaming rule: the client CONSUMES the server's SSE stream and
// NEVER polls. `EventSource` can't issue a POST, so we open the stream with
// fetch(), read the ReadableStream, and parse `event:` / `data:` frames by hand.
//
// Frame grammar (text/event-stream): frames are separated by a blank line; each
// frame has an `event: <name>` line and one or more `data: <json>` lines.

import type { RawFrame } from "./types";

export interface RunStreamHandlers {
  onFrame: (frame: RawFrame) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

export interface RunStreamOptions {
  profile: string;
  intent: string;
  user_id: string;
  user_email: string;
  model: string;
}

export interface RunStreamController {
  abort: () => void;
}

export function startErrandStream(
  url: string,
  options: RunStreamOptions,
  handlers: RunStreamHandlers,
): RunStreamController {
  const controller = new AbortController();

  (async () => {
    let res: Response;
    try {
      res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options),
        signal: controller.signal,
      });
    } catch (err) {
      if (!controller.signal.aborted) {
        handlers.onError?.(
          `Could not reach the agent backend. ${(err as Error).message}`,
        );
      }
      return;
    }

    if (!res.ok || !res.body) {
      handlers.onError?.(`Backend responded ${res.status}.`);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Split complete frames on the blank-line delimiter.
        let sep: number;
        // Handle both \n\n and \r\n\r\n.
        while (
          (sep = indexOfFrameBoundary(buffer)) !== -1
        ) {
          const rawFrame = buffer.slice(0, sep);
          buffer = buffer.slice(sep).replace(/^(\r?\n){2}/, "");
          const parsed = parseFrame(rawFrame);
          if (parsed) handlers.onFrame(parsed);
        }
      }
      handlers.onDone?.();
    } catch (err) {
      if (!controller.signal.aborted) {
        handlers.onError?.((err as Error).message);
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
