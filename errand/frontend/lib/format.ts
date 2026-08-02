// Small formatting helpers. Prices are cents integers on the wire.

export function money(cents: number | undefined | null): string {
  if (cents == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(cents / 100);
}

// Compact time-of-day for the audit log, e.g. "09:01:06.337".
export function clock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  const ms = String(d.getMilliseconds()).padStart(3, "0");
  return `${hh}:${mm}:${ss}.${ms}`;
}

// Senso citation snippets arrive as raw markdown fragments. Flatten to a short
// single-line preview for the source chip's expanded body.
export function tidySnippet(s: string, max = 220): string {
  const clean = s
    .replace(/^#+\s*/gm, "")
    .replace(/\*\*/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return clean.length > max ? clean.slice(0, max).trimEnd() + "…" : clean;
}
