import type {
  ApprovalGate,
  AuditSink,
  ContextBroker,
  MailBroker,
  PaymentBroker,
  ProfileKind,
  ShopperBroker,
} from "@/lib/contracts";

export interface ErrandDeps {
  context: ContextBroker;
  shopper: ShopperBroker;
  payment: PaymentBroker;
  mail: MailBroker;
  approval: ApprovalGate;
  audit: AuditSink;
}

export interface ErrandInput {
  profile: ProfileKind;
  intent: string;
  user: { id: string; email: string };
}

export type ErrandOutcome =
  | { kind: "completed"; orderId: string; totalCents: number; confirmationOrderId?: string }
  | { kind: "aborted"; reason: string }
  | { kind: "failed"; reason: string };

const now = () => new Date().toISOString();

/**
 * The persona-agnostic engine. Same code for business procurement and personal
 * errands — only the `profile` (and thus the grounded context) differs.
 *
 * Depends only on broker interfaces, so mocks and real implementations are
 * fully interchangeable. Every meaningful step is recorded to the AuditSink.
 */
export async function runErrand(
  deps: ErrandDeps,
  input: ErrandInput,
): Promise<ErrandOutcome> {
  const { context, shopper, payment, mail, approval, audit } = deps;
  const rec = (step: string, detail: string, data?: unknown) =>
    audit.record({ at: now(), step, detail, data });

  // 0. Ensure the agent has a real inbox (used as the order/contact email).
  const inbox = await mail.ensureInbox();
  rec("inbox.ready", `Agent inbox: ${inbox.address}`, inbox);

  // 1. Ground the decision in verified context (Senso).
  const ctx = await context.getContext(input.profile, input.intent);
  rec(
    "context.loaded",
    `Loaded ${input.profile} context: budget $${(ctx.budgetCents / 100).toFixed(2)}, ${ctx.rules.length} rules`,
    ctx,
  );

  // Guardrail: no approved merchant → refuse, do not improvise.
  const merchant = ctx.approvedMerchants[0];
  if (!merchant) {
    rec("context.no_merchant", "No approved merchant for this intent; stopping.");
    return { kind: "aborted", reason: "No approved merchant in context." };
  }

  // 2. Shop the merchant in a real browser; park on checkout.
  const cart = await shopper.buildCart(merchant.url, input.intent, ctx);
  rec(
    "cart.built",
    `Cart: ${cart.items.length} items, total $${(cart.totalCents / 100).toFixed(2)}`,
    cart,
  );

  if (cart.totalCents > ctx.budgetCents) {
    rec("cart.over_budget", "Cart exceeds budget after best effort; stopping.");
    return { kind: "aborted", reason: "Cart exceeds budget." };
  }

  // 3. Create the Prava session (pins merchant + amount).
  const session = await payment.createSession({
    merchant,
    totalCents: cart.totalCents,
    user: { id: input.user.id, email: inbox.address },
    items: cart.items,
  });
  rec("payment.session", `Prava session ${session.sessionId} created`, session);

  // 4. Human-in-the-loop: approve the spend (+ passkey in the real UI).
  const { approved } = await approval.requestApproval({ context: ctx, cart, session });
  if (!approved) {
    rec("approval.denied", "Operator declined the spend.");
    return { kind: "aborted", reason: "Spend not approved." };
  }
  rec("approval.granted", "Operator approved the spend (passkey).");

  // 5. Poll for the one-time credential.
  let credential;
  for (let i = 0; i < 20; i++) {
    const res = await payment.pollCredential(session.sessionId);
    if (res.status === "completed") {
      credential = res.credential;
      break;
    }
    if (res.status === "failed") {
      rec("payment.failed", res.error.message, res.error);
      await payment.reportStatus(session.sessionId, `unknown`, "DECLINED").catch(() => {});
      return { kind: "failed", reason: `Payment failed: ${res.error.message}` };
    }
    // pending → brief wait (short in mock)
    await new Promise((r) => setTimeout(r, 50));
  }
  if (!credential) {
    rec("payment.timeout", "Credential not ready in time.");
    return { kind: "failed", reason: "Payment credential timed out." };
  }
  rec("payment.credential", "One-time credential issued.", {
    last4: credential.token.slice(-4),
    txnRefId: credential.txnRefId,
  });

  // 6. Complete the real checkout with the credential.
  const startedAt = now();
  const order = await shopper.completeCheckout(cart.checkout, credential);
  rec("checkout.completed", order.confirmationText, order);

  // 7. Report outcome to Prava (required).
  await payment.reportStatus(session.sessionId, credential.txnRefId, "APPROVED");
  rec("payment.reported", "Reported APPROVED to Prava.");

  // 8. Close the loop: catch the confirmation email in the agent's inbox.
  const confirmation = await mail.waitForConfirmation({
    merchant: merchant.url,
    sinceIso: startedAt,
    timeoutMs: 30_000,
  });
  rec(
    "mail.confirmation",
    confirmation.matched
      ? `Confirmation email received (${confirmation.orderId ?? "no id parsed"})`
      : "No confirmation email matched.",
    confirmation,
  );

  return {
    kind: "completed",
    orderId: order.orderId,
    totalCents: cart.totalCents,
    confirmationOrderId: confirmation.orderId,
  };
}
