import type { ProfileKind, PurchaseContext } from "@/lib/contracts";

/**
 * Seed content for the two demo profiles. In production these live as documents
 * in a Senso knowledge base and are retrieved with citations; here they are the
 * ground truth the mock ContextBroker returns and the fixtures the real Senso
 * KB should be seeded with.
 */
export const PROFILE_SEED: Record<ProfileKind, PurchaseContext> = {
  business: {
    profile: "business",
    approvedMerchants: [
      { name: "Demo Pantry Co", url: "https://demo-pantry.example.com" },
    ],
    budgetCents: 20000, // $200
    rules: [
      "Prefer approved brands: Blue Bottle, Clif, LaCroix.",
      "No energy drinks.",
      "Stay at or under the stated budget.",
      "Buy pantry staples: coffee, snack bars, sparkling water.",
    ],
    citations: [
      {
        source: "Procurement Policy v3 §2 (Approved Vendors)",
        snippet:
          "Pantry restocking must use approved vendors and preferred brands; per-order cap $200.",
      },
    ],
  },
  personal: {
    profile: "personal",
    approvedMerchants: [
      { name: "Demo Pantry Co", url: "https://demo-pantry.example.com" },
    ],
    budgetCents: 6000, // $60
    rules: [
      "Household favourites: oat milk, dark roast coffee, sparkling water.",
      "Avoid anything with added sugar where possible.",
      "Keep it under my weekly budget.",
    ],
    citations: [
      {
        source: "My Preferences (personal profile)",
        snippet:
          "Weekly grocery budget $60; prefer oat milk and dark roast; low sugar.",
      },
    ],
  },
};
