import { describe, expect, it } from "vitest";
import { DEMO_PURCHASE, DEMO_TOTAL_CENTS, landingDemoReducer } from "./landingDemo";

describe("landing demo", () => {
  it("uses internally consistent line-item math", () => {
    expect(DEMO_TOTAL_CENTS).toBe(
      DEMO_PURCHASE.items.reduce((sum, item) => sum + item.unitCents * item.quantity, 0),
    );
  });

  it("supports approve, decline, and replay", () => {
    expect(landingDemoReducer("held", { type: "approve" })).toBe("approved");
    expect(landingDemoReducer("held", { type: "decline" })).toBe("declined");
    expect(landingDemoReducer("approved", { type: "replay" })).toBe("held");
    expect(landingDemoReducer("declined", { type: "replay" })).toBe("held");
  });
});
