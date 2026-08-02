export type LandingDemoState = "held" | "approved" | "declined";
export type LandingDemoAction = { type: "approve" | "decline" | "replay" };

export const DEMO_PURCHASE = {
  merchant: "Northwind Provisions",
  request: "Restock the office pantry under $100, approved brands only.",
  policy: "Approved merchant · Pantry budget $100",
  items: [
    { name: "Oat milk", detail: "6 × 1L", unitCents: 390, quantity: 6 },
    { name: "Dark roast beans", detail: "1kg", unitCents: 2800, quantity: 1 },
    { name: "Sparkling water", detail: "24 × 330ml", unitCents: 1960, quantity: 1 },
  ],
} as const;

export const DEMO_TOTAL_CENTS = DEMO_PURCHASE.items.reduce(
  (sum, item) => sum + item.unitCents * item.quantity,
  0,
);

export function landingDemoReducer(
  state: LandingDemoState,
  action: LandingDemoAction,
): LandingDemoState {
  if (action.type === "replay") return "held";
  if (state !== "held") return state;
  return action.type === "approve" ? "approved" : "declined";
}
