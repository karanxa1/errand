/**
 * Broker registry. Wires the orchestrator to real or mock implementations based
 * on env. Parallel agents implement the real brokers in sibling files:
 *   - prava.ts   (PravaPaymentBroker)    — DONE
 *   - senso.ts   (SensoContextBroker)    — pending
 *   - shopper.ts (StagehandShopperBroker)— pending (shopper agent)
 *   - mail.ts    (AgentMailBroker)       — pending (mail agent)
 * Until a real broker lands, the mock is used, so the app always runs.
 */
export * from "./mock";
