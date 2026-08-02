import type {
  AuditEvent,
  AuditSink,
  ApprovalGate,
  CartResult,
  ContextBroker,
  CreateSessionInput,
  CreateSessionResult,
  InboxMessage,
  MailBroker,
  OrderConfirmation,
  OrderResult,
  PaymentBroker,
  PollCredentialResult,
  ProfileKind,
  PurchaseContext,
  ShopperBroker,
  TxnStatus,
} from "@/lib/contracts";
import { PROFILE_SEED } from "@/lib/profiles/seed";

/**
 * Mock implementations of every broker. These make the entire flow runnable
 * with zero API keys, and serve as the stubs parallel agents build the real
 * versions against. Each mock honours its interface exactly.
 */

// ── ContextBroker (mock Senso) ─────────────────────────────────────────────
export class MockContextBroker implements ContextBroker {
  async getContext(profile: ProfileKind, _intent: string): Promise<PurchaseContext> {
    // Real impl: query Senso KB with the intent, return cited rules.
    return structuredClone(PROFILE_SEED[profile]);
  }
}

// ── ShopperBroker (mock Stagehand/Browser Run) ─────────────────────────────
export class MockShopperBroker implements ShopperBroker {
  async buildCart(
    merchantUrl: string,
    _intent: string,
    context: PurchaseContext,
  ): Promise<CartResult> {
    // Real impl: drive the merchant with Stagehand act/extract/observe.
    const items = [
      { name: "Blue Bottle Coffee 12oz", qty: 2, priceCents: 1800 },
      { name: "Clif Bars (12 pack)", qty: 1, priceCents: 1500 },
      { name: "LaCroix Sparkling Water (24)", qty: 1, priceCents: 1200 },
    ];
    const totalCents = items.reduce((s, i) => s + i.qty * i.priceCents, 0);
    if (totalCents > context.budgetCents) {
      // Trim to respect budget — proves the guardrail in the mock too.
      items.pop();
    }
    const finalItems = items;
    const finalTotal = finalItems.reduce((s, i) => s + i.qty * i.priceCents, 0);
    return {
      items: finalItems,
      totalCents: finalTotal,
      checkout: {
        merchantUrl,
        items: finalItems,
        sessionRef: `mock-browser-${Date.now()}`,
      },
    };
  }

  async completeCheckout(
    checkout: { merchantUrl: string },
    _credential: unknown,
  ): Promise<OrderResult> {
    // Real impl: type token + dynamic CVV into the parked checkout page.
    const orderId = `ORD-${Math.floor(Math.random() * 1_000_000)}`;
    return {
      orderId,
      confirmationText: `Order ${orderId} placed at ${checkout.merchantUrl}`,
    };
  }
}

// ── PaymentBroker (mock Prava) ─────────────────────────────────────────────
export class MockPaymentBroker implements PaymentBroker {
  private polls = new Map<string, number>();

  async createSession(input: CreateSessionInput): Promise<CreateSessionResult> {
    const sessionId = `sess_mock_${Date.now()}`;
    return {
      sessionId,
      iframeUrl: `https://sandbox.prava.space/iframe?session=${sessionId}&amt=${input.totalCents}`,
    };
  }

  async pollCredential(sessionId: string): Promise<PollCredentialResult> {
    // Simulate the pending → completed transition Prava exhibits.
    const count = (this.polls.get(sessionId) ?? 0) + 1;
    this.polls.set(sessionId, count);
    if (count < 2) return { status: "pending" };
    return {
      status: "completed",
      credential: {
        token: "4111111111111111",
        dynamicCvv: "123",
        expiryMonth: "12",
        expiryYear: "2029",
        txnRefId: `txn_${sessionId}`,
      },
    };
  }

  async reportStatus(_sessionId: string, _txnRefId: string, _status: TxnStatus): Promise<void> {
    // Real impl: POST /v1/sessions/{id}/report-status
  }
}

// ── MailBroker (mock AgentMail) ────────────────────────────────────────────
export class MockMailBroker implements MailBroker {
  private address = "errand-agent@demo.agentmail.to";

  async ensureInbox(): Promise<{ address: string }> {
    return { address: this.address };
  }

  async waitForConfirmation(opts: {
    merchant: string;
    sinceIso: string;
    timeoutMs: number;
  }): Promise<OrderConfirmation> {
    // Real impl: poll inboxes.messages.list until the confirmation arrives.
    const raw: InboxMessage = {
      id: `msg_${Date.now()}`,
      from: `orders@${opts.merchant.replace(/^https?:\/\//, "")}`,
      subject: "Your order is confirmed",
      text: "Thanks for your order! Order ORD-123456 has been received.",
      receivedAt: new Date().toISOString(),
    };
    const orderId = raw.text.match(/ORD-\d+/)?.[0];
    return { matched: true, orderId, merchant: opts.merchant, raw };
  }

  async listMessages(_limit = 10): Promise<InboxMessage[]> {
    return [];
  }

  async reply(_messageId: string, _text: string): Promise<void> {
    // Stretch.
  }
}

// ── ApprovalGate (auto-approve for tests/CLI) ──────────────────────────────
export class AutoApproveGate implements ApprovalGate {
  async requestApproval(): Promise<{ approved: boolean }> {
    return { approved: true };
  }
}

// ── AuditSink (in-memory) ──────────────────────────────────────────────────
export class MemoryAuditSink implements AuditSink {
  private events: AuditEvent[] = [];
  record(event: AuditEvent): void {
    this.events.push(event);
  }
  all(): AuditEvent[] {
    return [...this.events];
  }
}
