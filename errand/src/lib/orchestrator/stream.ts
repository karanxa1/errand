import type { AuditEvent, AuditSink } from "@/lib/contracts";

/**
 * Streaming transport for orchestrator events.
 *
 * Client <-> server is streaming-only, NO client polling. The orchestrator
 * records an AuditEvent at every meaningful step (context.loaded, cart.built,
 * approval.granted, payment.credential, checkout.completed, mail.confirmation,
 * ...). A StreamingAuditSink pushes each event to the client the instant it
 * happens, so the UI reflects real-time progress without ever polling.
 *
 * Server <-> third-party polling (Prava payment-result, AgentMail inbox) is
 * internal to the brokers and hidden from the client; its results surface here
 * as streamed events.
 */

export type EmitFn = (event: AuditEvent) => void;

/** AuditSink that forwards every event to an emit callback (and keeps a copy). */
export class StreamingAuditSink implements AuditSink {
  private events: AuditEvent[] = [];
  constructor(private readonly emit: EmitFn) {}
  record(event: AuditEvent): void {
    this.events.push(event);
    this.emit(event);
  }
  all(): AuditEvent[] {
    return [...this.events];
  }
}

/** SSE frame for one event. Use in a Next.js Route Handler ReadableStream. */
export function sseFrame(event: AuditEvent): string {
  return `event: ${event.step}\ndata: ${JSON.stringify(event)}\n\n`;
}

/**
 * Build a streaming Response body for a Route Handler. `run` receives an `emit`
 * it calls (via a StreamingAuditSink) for each step; the returned ReadableStream
 * flushes each event to the client immediately, then closes.
 *
 * Example (app/api/errand/route.ts):
 *   return sseResponse(async (emit) => {
 *     const audit = new StreamingAuditSink(emit);
 *     await runErrand({ ...brokers, audit }, input);
 *   });
 */
export function sseResponse(
  run: (emit: EmitFn) => Promise<void>,
): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const emit: EmitFn = (event) => {
        controller.enqueue(encoder.encode(sseFrame(event)));
      };
      try {
        await run(emit);
        controller.enqueue(encoder.encode("event: done\ndata: {}\n\n"));
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        controller.enqueue(
          encoder.encode(`event: error\ndata: ${JSON.stringify({ message })}\n\n`),
        );
      } finally {
        controller.close();
      }
    },
  });
  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
