# Autopilot Procurement — Design

**Event:** Agentic Commerce Hackathon (Aug 1–2, 2026)
**Date:** 2026-08-02
**Status:** Approved for planning

## One-line pitch

A voice-driven agent that restocks a business under budget and within approved
vendors: it decides from verified policy (Senso), shops a real merchant in a real
browser (Cloudflare Browser Run + Stagehand), pays with a one-time,
passkey-approved card credential (Prava), and closes the loop through its own real
email inbox (AgentMail) — with the human approving every spend.

## User & problem

**User:** an office / ops manager responsible for recurring supply purchases.

**Problem:** restocking is manual and repetitive — browse a store, remember which
brands are approved, stay under budget, check out. It is exactly the kind of
bounded, policy-governed task an agent should own, but nobody trusts an agent with
a card. The trust gap (what can it spend, on what, and did it actually happen) is
the real problem.

**Why this wins:** the policy-as-verified-context angle is the strongest possible
Senso story; the spend-approval moment is a memorable voice demo; and the audit
trail directly answers Visa's "controls and trust" bar. It is also a real product
an ops team would keep using.

## Success criteria (demo-able)

A judge watches this happen end to end:

1. Operator speaks an intent ("restock the office pantry, under $200, approved
   brands only").
2. Agent states its plan back, grounded in cited policy from Senso.
3. Agent drives a real merchant checkout in a browser and assembles a cart with a
   real total.
4. UI shows the cart + total; operator approves; Prava issues a scoped credential
   after passkey.
5. Agent completes the real checkout and shows an order confirmation.
6. An audit log explains every choice ("chose brand X because policy Y; spent $Z;
   approved by passkey at T").

## Tracks targeted

- **Prava finalist** (required) — Prava is the core commercial action.
- **Senso** — verified policy context materially drives the buy decision.
- **Visa Intelligent Commerce** — scoped credential + passkey + audit = controls.
- **Localhost (startup-ready)** — a real ops product.
- **OpenAI** — reasoning + tool orchestration.
- **Sarvam (optional)** — Hindi/Hinglish voice for the India-first angle.

Note: Cloudflare (Browser Run) and AgentMail are core infrastructure here even
though neither is a named prize track; they carry the "real browser + real email"
capability that makes the agent autonomous.

## Architecture

```
                         ┌──────────────────────────────┐
        voice in/out     │        VoiceController        │
  operator ⇄ mic ───────▶│  Deepgram Voice Agent (WS)    │
                         │  function-calling → tools     │
                         └──────────────┬───────────────┘
                                        │ tool calls
         ┌───────────────┬─────────────┼─────────────┬───────────────┐
         ▼               ▼             ▼             ▼               ▼
 ┌────────────┐ ┌────────────────┐ ┌──────────────┐ ┌──────────────────┐
 │PolicyBroker│ │  ShopperAgent  │ │ PaymentBroker│ │    MailBroker    │
 │  (Senso)   │ │(Stagehand on CF│ │   (Prava)    │ │   (AgentMail)    │
 │getPurchase-│ │  Browser Run)  │ │createSession/│ │ ensureInbox /    │
 │ Policy()   │ │ buildCart() /  │ │pollCredential│ │ waitForConfirm / │
 │→ cited rule│ │completeCheckout│ │/ reportStatus│ │ listMessages     │
 └────────────┘ └────────────────┘ └──────────────┘ └──────────────────┘
         │               │             │             │
         └────────────── shared session state ───────┘
                                        │
                                        ▼
                        ┌──────────────────────────────┐
                        │      web (assistant-ui)        │
                        │ cart card · approval + Prava   │
                        │ iframe (passkey) · audit log   │
                        └──────────────────────────────┘
```

**Runtime:** Next.js app (frontend + API routes / server actions). A Cloudflare
Worker hosts the Browser Run session for Stagehand. Server-side secrets
(Prava secret key, Senso key, Deepgram key) never reach the client.

## Component contracts

These interfaces are the seams. Each can be built and tested independently by a
separate agent against a stub of the others.

### PolicyBroker (Senso)
```ts
type Citation = { source: string; snippet: string };
type PurchasePolicy = {
  approvedMerchants: { name: string; url: string }[];
  budgetCents: number;
  brandRules: string[];        // e.g. "prefer brand X", "no brand Y"
  citations: Citation[];
};
getPurchasePolicy(intent: string): Promise<PurchasePolicy>;
```
Backed by a Senso knowledge base seeded with one procurement-policy document.

### ShopperAgent (Stagehand on Cloudflare Browser Run)
```ts
type CartItem = { name: string; qty: number; priceCents: number };
type CheckoutState = { merchantUrl: string; items: CartItem[]; sessionRef: string };
type CartResult = { items: CartItem[]; totalCents: number; checkout: CheckoutState };
type OrderResult = { orderId: string; confirmationText: string; screenshotUrl?: string };

buildCart(merchantUrl: string, intent: string, policy: PurchasePolicy): Promise<CartResult>;
completeCheckout(
  checkout: CheckoutState,
  credential: PaymentCredential
): Promise<OrderResult>;
```
Uses Stagehand `act/extract/observe` for resilient navigation. Fills the merchant
checkout with the Prava credential in `completeCheckout`.

### PaymentBroker (Prava)
```ts
type PaymentCredential = {
  token: string; dynamicCvv: string; expiryMonth: string; expiryYear: string;
  txnRefId: string;
};
createSession(input: {
  merchant: { name: string; url: string };
  totalCents: number;
  user: { id: string; email: string };
  items: CartItem[];
}): Promise<{ sessionId: string; iframeUrl: string }>;
pollCredential(sessionId: string): Promise<
  | { status: "pending" }
  | { status: "completed"; credential: PaymentCredential }
  | { status: "failed"; error: { code: string; message: string } }
>;
reportStatus(sessionId: string, txnRefId: string, status: "APPROVED" | "DECLINED"): Promise<void>;
```
Maps directly to Prava `POST /v1/sessions`, `GET /v1/sessions/{id}/payment-result`
(poll every 3s), and `POST /v1/sessions/{id}/report-status`. Credential fields live
on `transactions[0].line_items[0]`. `createSession` pins the merchant + amount, so
the issued token is merchant-scoped.

### VoiceController (Deepgram)
Deepgram Voice Agent over WebSocket with function-calling. Registers functions
that map to the brokers above (`getPurchasePolicy`, `buildCart`, `createSession`,
`waitForConfirmation`), plus an `approveSpend` gate that surfaces the cart to the
UI and waits for passkey approval before `pollCredential` runs. Copies the
function-call pattern from the Deepgram `021-twilio-voice-agent-node` example.

### MailBroker (AgentMail)
```ts
type InboxMessage = {
  id: string; from: string; subject: string; text: string;
  receivedAt: string; attachments?: { filename: string; url: string }[];
};
type OrderConfirmation = {
  matched: boolean; orderId?: string; totalCents?: number;
  merchant?: string; raw: InboxMessage;
};

ensureInbox(): Promise<{ address: string }>;              // agent's real email
waitForConfirmation(opts: {                                // poll for order email
  merchant: string; sinceIso: string; timeoutMs: number;
}): Promise<OrderConfirmation>;
listMessages(limit?: number): Promise<InboxMessage[]>;     // for audit / vendor replies
reply(messageId: string, text: string): Promise<void>;     // stretch: two-way threading
```
Backed by AgentMail (`inboxes.create`, `inboxes.messages.list`, `messages.reply`).
The inbox address is used as the shipping/order email so the merchant's
confirmation lands where the agent can read it. All-CF alternative: Cloudflare
Email Routing + an `email()` Worker handler with `PostalMime.parse`.

### web (assistant-ui / Next.js)
Renders tool calls as React components: a **cart card**, an **approval panel**
that mounts the Prava iframe (`PravaSDK.collectPAN` with `sessionToken` +
`iframeUrl`) for passkey, and a live **audit log**. assistant-ui's inline
human-approval feature is used for the approve-spend step.

## Data flow (the critical handoff)

1. `getPurchasePolicy(intent)` → policy + citations (Senso).
2. `buildCart(merchant, intent, policy)` → cart + total + `checkout` state
   (browser stays open on the checkout page).
3. UI shows cart; operator approves → `createSession(merchant, total, user, items)`
   → `{ sessionId, iframeUrl }`.
4. Prava iframe mounts; operator completes passkey (sandbox OTP `456789` first
   time).
5. Server `pollCredential(sessionId)` every 3s until `completed` → `credential`.
6. `completeCheckout(checkout, credential)` types token + dynamic CVV into the
   already-open merchant checkout → `orderId`.
7. `reportStatus(sessionId, txnRefId, "APPROVED")` (required).
8. `waitForConfirmation({ merchant, sinceIso })` — agent's inbox (AgentMail)
   catches the order confirmation email and matches it to the order.
9. Audit log renders the whole chain, including the confirmation email.

The agent's email address (from `ensureInbox()`) is used as the order/contact
email during `buildCart`, so the merchant's confirmation is deliverable to the
agent's own inbox.

## Error handling

- **Merchant checkout flaky / captcha:** validated merchant chosen up front; a
  controlled fallback store guarantees the demo completes. `buildCart` and
  `completeCheckout` return typed failures the VoiceController can narrate.
- **Prava `failed` status:** surface `error.message` in the UI; still call
  `reportStatus` with `DECLINED` so the transaction is not stuck.
- **Policy has no approved merchant for the intent:** agent says so and stops —
  it does not improvise a purchase (this is a feature, not a bug: it proves the
  guardrail).
- **Voice STT mis-hears amount/brand:** agent reads the parsed intent back before
  acting; approval step is the final backstop.

## Testing

- Each broker has a thin integration test against sandbox (Senso KB query, Prava
  sandbox session with test card, Stagehand against the validated merchant).
- A scripted end-to-end run (no voice) drives the full chain headless to prove the
  handoff before wiring voice on top.
- Sandbox test data: card `456789` OTP, Prava `sk_test_` / `pk_test_` keys.

## Ruthless MVP scope

**In:** one intent (restock pantry), one validated merchant, one Senso policy doc,
voice → shop → approve → pay → confirm, agent inbox catches the order confirmation,
audit log.

**Email v1 lite:** `ensureInbox` + `waitForConfirmation` (capture the order email
and file it). Two-way vendor threading (`reply`) and email-as-input-channel are
**stretch**.

**Out (v1):** multi-merchant comparison, the Cloudflare Sandbox "real computer"
(Lane C), recurring/scheduled buys, multi-user. Add only if the core loop is solid
with time to spare.

## Reference repos (fork map)

| Layer | Repo | Use |
|---|---|---|
| Payment | `Prava-Payments/prava-skills` | SDK templates, session API, test cards |
| Runtime/approval | `cloudflare/agents-starter` | tool + human-approval pattern |
| Browser | `browserbase/stagehand` | act/extract/observe on CF Browser Run |
| Voice | `deepgram-devs/deepgram-ai-agent-demo` + `deepgram/examples/021-...` | WS voice + function calling |
| UI | `assistant-ui/assistant-ui` | generative UI + inline approval |
| Email | AgentMail SDK (`agentmail`) | agent's real inbox: create, read, reply |
| Email (alt) | Cloudflare Email Routing + Email Workers | all-CF inbound `email()` + `PostalMime` |
| (Lane C only) | `cloudflare/sandbox-sdk` | real-computer, deferred |

## Open decisions deferred (not blocking)

- Deepgram-only vs. Deepgram + Sarvam Indic voice — decide after core loop works.
- Exact merchant — pick from handbook UCP/MCP lists during "validate merchant"
  step, before any component build.
