/**
 * Shared domain types for the Errand agent.
 *
 * This is the single source of truth for the seams between components.
 * Every broker implements an interface here; the orchestrator depends only
 * on these interfaces, never on concrete implementations. Swapping a mock
 * broker for a real one must require zero changes outside the broker file.
 */

// ── Context / profiles ─────────────────────────────────────────────────────

/** Who the agent is acting for. Business = procurement, personal = errands. */
export type ProfileKind = "business" | "personal";

export interface Citation {
  /** Human-readable source name, e.g. "Procurement Policy v3" or a URL. */
  source: string;
  /** The exact snippet that grounds a rule. */
  snippet: string;
}

/**
 * The grounded context that governs a purchase. Same shape for both profiles;
 * only the content differs (procurement policy vs. personal preferences).
 */
export interface PurchaseContext {
  profile: ProfileKind;
  approvedMerchants: { name: string; url: string }[];
  budgetCents: number;
  /** Free-form rules the shopper must honour, e.g. "prefer oat milk", "no brand X". */
  rules: string[];
  citations: Citation[];
}

// ── Shopping ────────────────────────────────────────────────────────────────

export interface CartItem {
  name: string;
  qty: number;
  priceCents: number;
}

/**
 * Opaque handle to a browser session parked on the merchant's checkout page.
 * `sessionRef` identifies the live Browser Run / Stagehand session so a later
 * completeCheckout() can resume the same page.
 */
export interface CheckoutState {
  merchantUrl: string;
  items: CartItem[];
  sessionRef: string;
}

export interface CartResult {
  items: CartItem[];
  totalCents: number;
  checkout: CheckoutState;
}

export interface OrderResult {
  orderId: string;
  confirmationText: string;
  screenshotUrl?: string;
}

// ── Payment (Prava) ──────────────────────────────────────────────────────────

/** One-time, merchant-scoped card credential returned by Prava. */
export interface PaymentCredential {
  token: string;
  dynamicCvv: string;
  expiryMonth: string; // "MM"
  expiryYear: string; // "YYYY"
  txnRefId: string;
}

export interface CreateSessionInput {
  merchant: { name: string; url: string };
  totalCents: number;
  user: { id: string; email: string };
  items: CartItem[];
}

export interface CreateSessionResult {
  sessionId: string;
  iframeUrl: string;
}

export type PollCredentialResult =
  | { status: "pending" }
  | { status: "completed"; credential: PaymentCredential }
  | { status: "failed"; error: { code: string; message: string } };

export type TxnStatus = "APPROVED" | "DECLINED";

// ── Email (AgentMail) ─────────────────────────────────────────────────────────

export interface InboxMessage {
  id: string;
  from: string;
  subject: string;
  text: string;
  receivedAt: string; // ISO
  attachments?: { filename: string; url: string }[];
}

export interface OrderConfirmation {
  matched: boolean;
  orderId?: string;
  totalCents?: number;
  merchant?: string;
  raw: InboxMessage;
}

// ── Broker interfaces (the seams) ─────────────────────────────────────────────

export interface ContextBroker {
  /** Grounded context for an intent, scoped to a profile. Backed by Senso. */
  getContext(profile: ProfileKind, intent: string): Promise<PurchaseContext>;
}

export interface ShopperBroker {
  /** Navigate a merchant, assemble a cart honouring the context, park on checkout. */
  buildCart(
    merchantUrl: string,
    intent: string,
    context: PurchaseContext,
  ): Promise<CartResult>;
  /** Resume the parked checkout and pay with the Prava credential. */
  completeCheckout(
    checkout: CheckoutState,
    credential: PaymentCredential,
  ): Promise<OrderResult>;
}

export interface PaymentBroker {
  createSession(input: CreateSessionInput): Promise<CreateSessionResult>;
  pollCredential(sessionId: string): Promise<PollCredentialResult>;
  reportStatus(sessionId: string, txnRefId: string, status: TxnStatus): Promise<void>;
}

export interface MailBroker {
  /** Ensure the agent has a real inbox; return its address. */
  ensureInbox(): Promise<{ address: string }>;
  /** Poll for the order-confirmation email and match it to the order. */
  waitForConfirmation(opts: {
    merchant: string;
    sinceIso: string;
    timeoutMs: number;
  }): Promise<OrderConfirmation>;
  listMessages(limit?: number): Promise<InboxMessage[]>;
  /** Stretch: reply in-thread to a vendor. */
  reply(messageId: string, text: string): Promise<void>;
}

// ── Approval gate ─────────────────────────────────────────────────────────────

/**
 * The human-in-the-loop gate. In the real app this resolves when the operator
 * approves the cart in the UI and completes the Prava passkey. In tests it can
 * auto-approve. Returns false to abort the purchase.
 */
export interface ApprovalGate {
  requestApproval(input: {
    context: PurchaseContext;
    cart: CartResult;
    session: CreateSessionResult;
  }): Promise<{ approved: boolean }>;
}

// ── Audit ─────────────────────────────────────────────────────────────────────

export interface AuditEvent {
  at: string; // ISO
  step: string;
  detail: string;
  data?: unknown;
}

export interface AuditSink {
  record(event: AuditEvent): void;
  all(): AuditEvent[];
}
