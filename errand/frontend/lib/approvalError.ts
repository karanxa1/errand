// approvalError — turn a rejected /approve response into the sentence the
// operator actually needs.
//
// Both approval paths (useErrandRun over /api/errand/{run_id}/approve, useChat
// over /api/conversations/{id}/approve) used to fire-and-forget: an await with
// no `res.ok` check, so a rejected verdict looked exactly like an accepted one
// and the card sat on "Approving…" forever. The backend already distinguishes
// "expired code" from "binding limit reached" from "card verification failed"
// from "device not supported"; each of those has a different fix, and only one
// of them is worth escalating. Flattening them into "Payment failed" is what
// turns a ten-second recovery into a support ticket, so this preserves whatever
// the backend actually said.
//
// Two failure shapes exist on this route:
//   * a non-2xx — FastAPI's HTTPException body is {detail}, richer errors carry
//     {code, message}
//   * a 200 carrying {ok: false, reason} — the "no pending approval for this
//     run" answer, which is a real failure wearing a success status code and is
//     the one a bare res.ok check still misses.

// Null when the backend accepted the verdict; otherwise the message to show.
export async function approvalFailure(res: Response): Promise<string | null> {
  let body: Record<string, unknown> | null = null;
  try {
    body = (await res.json()) as Record<string, unknown>;
  } catch {
    /* empty or non-JSON body — the status is all we have */
  }

  const str = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : null);
  const code = str(body?.code);
  const detail = str(body?.message) ?? str(body?.detail) ?? str(body?.reason);

  if (res.ok) {
    // A 200 that says ok:false is still a refusal.
    if (body && body.ok === false) {
      return detail
        ? `The backend did not record the approval: ${detail}`
        : "The backend did not record the approval.";
    }
    return null;
  }

  if (code && detail) return `${code}: ${detail}`;
  if (code) return `The backend rejected the approval (${code}).`;
  if (detail) return `The backend rejected the approval: ${detail}`;
  // No code, no message — the status is the only fact we have, so report it
  // rather than inventing a cause.
  return `The backend rejected the approval (HTTP ${res.status}).`;
}
