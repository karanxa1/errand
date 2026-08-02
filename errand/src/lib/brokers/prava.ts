import type {
  CreateSessionInput,
  CreateSessionResult,
  PaymentBroker,
  PollCredentialResult,
  TxnStatus,
} from "@/lib/contracts";

/**
 * Real Prava PaymentBroker (sandbox or production, driven by env).
 *
 * Maps to:
 *   POST /v1/sessions
 *   GET  /v1/sessions/{id}/payment-result   (poll)
 *   POST /v1/sessions/{id}/report-status
 *
 * Uses the MERCHANT SECRET KEY server-side only. The credential fields live on
 * transactions[0].line_items[0]. createSession pins the merchant + amount, so
 * the issued token is merchant-scoped.
 */
export class PravaPaymentBroker implements PaymentBroker {
  constructor(
    private readonly secretKey: string,
    private readonly apiBase = "https://sandbox.api.prava.space",
  ) {
    if (!secretKey?.startsWith("sk_")) {
      throw new Error("PravaPaymentBroker: secret key must start with sk_");
    }
  }

  private headers() {
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${this.secretKey}`,
    };
  }

  async createSession(input: CreateSessionInput): Promise<CreateSessionResult> {
    const dollars = (input.totalCents / 100).toFixed(2);
    const body = {
      user_id: input.user.id,
      user_email: input.user.email,
      total_amount: dollars,
      currency: "USD",
      description: "Errand agent purchase",
      purchase_context: [
        {
          merchant_details: {
            name: input.merchant.name,
            url: input.merchant.url,
            country_code_iso2: "US",
          },
          product_details: input.items.map((it) => ({
            description: it.name,
            unit_price: (it.priceCents / 100).toFixed(2),
            quantity: it.qty,
          })),
          effective_until_minutes: 15,
        },
      ],
    };

    const res = await fetch(`${this.apiBase}/v1/sessions`, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Prava createSession failed (${res.status}): ${text}`);
    }
    const data = (await res.json()) as {
      session_id: string;
      iframe_url: string;
    };
    return { sessionId: data.session_id, iframeUrl: data.iframe_url };
  }

  async pollCredential(sessionId: string): Promise<PollCredentialResult> {
    const res = await fetch(
      `${this.apiBase}/v1/sessions/${sessionId}/payment-result`,
      { headers: this.headers() },
    );
    if (res.status === 404) return { status: "pending" };
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Prava pollCredential failed (${res.status}): ${text}`);
    }
    const data = (await res.json()) as {
      status: string;
      transactions?: {
        status: string;
        error?: { code: string; message: string };
        line_items?: {
          txn_ref_id: string;
          token: string | null;
          dynamic_cvv: string | null;
          expiry_month: string | null;
          expiry_year: string | null;
        }[];
      }[];
    };

    if (data.status === "completed") {
      const li = data.transactions?.[0]?.line_items?.[0];
      if (!li?.token || !li.dynamic_cvv || !li.expiry_month || !li.expiry_year) {
        return { status: "pending" }; // completed but credential not yet materialised
      }
      return {
        status: "completed",
        credential: {
          token: li.token,
          dynamicCvv: li.dynamic_cvv,
          expiryMonth: li.expiry_month,
          expiryYear: li.expiry_year,
          txnRefId: li.txn_ref_id,
        },
      };
    }
    if (data.status === "failed") {
      const err = data.transactions?.[0]?.error ?? {
        code: "UNKNOWN",
        message: "Payment failed",
      };
      return { status: "failed", error: err };
    }
    return { status: "pending" };
  }

  async reportStatus(
    sessionId: string,
    txnRefId: string,
    status: TxnStatus,
  ): Promise<void> {
    const res = await fetch(
      `${this.apiBase}/v1/sessions/${sessionId}/report-status`,
      {
        method: "POST",
        headers: this.headers(),
        body: JSON.stringify({ txn_ref_id: txnRefId, txn_status: status }),
      },
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Prava reportStatus failed (${res.status}): ${text}`);
    }
  }
}
