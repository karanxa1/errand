import {
  AutoApproveGate,
  MemoryAuditSink,
  MockContextBroker,
  MockMailBroker,
  MockPaymentBroker,
  MockShopperBroker,
} from "@/lib/brokers/mock";
import { runErrand, type ErrandDeps } from "@/lib/orchestrator/run-errand";
import type { ProfileKind } from "@/lib/contracts";

function makeDeps(): { deps: ErrandDeps; audit: MemoryAuditSink } {
  const audit = new MemoryAuditSink();
  const deps: ErrandDeps = {
    context: new MockContextBroker(),
    shopper: new MockShopperBroker(),
    payment: new MockPaymentBroker(),
    mail: new MockMailBroker(),
    approval: new AutoApproveGate(),
    audit,
  };
  return { deps, audit };
}

async function runFor(profile: ProfileKind, intent: string) {
  const { deps, audit } = makeDeps();
  console.log(`\n=== ${profile.toUpperCase()} — "${intent}" ===`);
  const outcome = await runErrand(deps, {
    profile,
    intent,
    user: { id: "u_demo", email: "operator@example.com" },
  });

  for (const e of audit.all()) {
    console.log(`  • [${e.step}] ${e.detail}`);
  }
  console.log("  → OUTCOME:", JSON.stringify(outcome));

  if (outcome.kind !== "completed") {
    throw new Error(`Expected completed outcome for ${profile}, got ${outcome.kind}`);
  }
  return outcome;
}

async function main() {
  await runFor("business", "Restock the office pantry, under $200, approved brands only.");
  await runFor("personal", "Order my usual groceries for the week.");
  console.log("\n✅ e2e mock flow passed for both profiles.\n");
}

main().catch((err) => {
  console.error("\n❌ e2e failed:", err);
  process.exit(1);
});
