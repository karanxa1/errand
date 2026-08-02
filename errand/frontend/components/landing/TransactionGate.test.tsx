// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import TransactionGate from "./TransactionGate";

describe("TransactionGate", () => {
  it("shows complete held-state information by default", () => {
    render(<TransactionGate />);
    expect(screen.getByText("Approval required")).toBeTruthy();
    expect(screen.getByText("$71.00")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve demo" })).toBeTruthy();
  });

  it("approves and replays without invoking external work", () => {
    render(<TransactionGate />);
    fireEvent.click(screen.getByRole("button", { name: "Approve demo" }));
    expect(screen.getByText("Approved demo")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Replay demo" }));
    expect(screen.getByText("Approval required")).toBeTruthy();
  });

  it("declines and states that nothing was charged", () => {
    render(<TransactionGate />);
    fireEvent.click(screen.getByRole("button", { name: "Decline demo" }));
    expect(screen.getByText("Nothing was charged.")).toBeTruthy();
  });
});
