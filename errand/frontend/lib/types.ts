// Domain types mirrored from the Python backend (app/contracts.py).
// The frontend never mutates these; it renders whatever the SSE stream sends.

export type ProfileKind = "business" | "personal";

export interface Merchant {
  name: string;
  url: string;
}

export interface Citation {
  source: string;
  snippet: string;
}

export interface PurchaseContext {
  profile: ProfileKind;
  approved_merchants: Merchant[];
  budget_cents: number;
  rules: string[];
  citations: Citation[];
}

export interface CartItem {
  name: string;
  qty: number;
  price_cents: number;
}

export interface CheckoutState {
  merchant_url: string;
  items: CartItem[];
  session_ref: string;
}

export interface CartResult {
  items: CartItem[];
  total_cents: number;
  checkout: CheckoutState;
}

export interface CreateSessionResult {
  session_id: string;
  iframe_url: string;
}

// Model selector option from GET /api/models.
export interface ModelOption {
  key: string;
  label: string;
  tagline: string;
  id: string;
}

// A single decoded SSE frame. `event` is the frame name; `data` is the parsed
// JSON payload. Audit-style events wrap their payload under `data.data`; raw
// events (run.started, approval.request, run.done, run.error) put fields at the
// top level. See lib/stream.ts for how each is normalised.
export interface RawFrame {
  event: string;
  data: Record<string, unknown>;
}

// Normalised audit-log entry the UI renders. Every stream frame becomes one of
// these (with the original payload preserved under `payload`).
export interface AuditEntry {
  id: number;
  at: string; // ISO timestamp (frame's own, or client receive time for raw events)
  step: string;
  detail: string;
  payload: Record<string, unknown> | null;
}

// The approval.request payload (raw, top-level fields).
export interface ApprovalRequest {
  run_id: string;
  context: PurchaseContext;
  cart: CartResult;
  session: CreateSessionResult;
}

// run.done payload.
export interface RunDone {
  kind: string;
  order_id?: string;
  total_cents?: number;
  confirmation_order_id?: string;
  reason?: string;
}
