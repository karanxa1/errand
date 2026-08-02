// The live browser view must never grow without bound or blank on a bad frame.
//
// browser.frame streams a screenshot per shop action. The reducer keeps only the
// LATEST frame (never a list), ignores an empty payload (so a stray frame can't
// wipe a good one), and clears the frame when a new run starts (so a fresh run
// never shows the previous run's last still). This pins all three.

import { describe, expect, it } from "vitest";
import { applyFrame, initialRunState } from "./errandReducer";

function frame(b64: string, caption = "", mime = "image/jpeg") {
  return { event: "browser.frame", data: { b64, caption, mime } };
}

describe("browser.frame", () => {
  it("stores the latest frame as a data URL with its caption", () => {
    const s = applyFrame(initialRunState, frame("AAA", "Added beans"));
    expect(s.browserFrame).toEqual({
      src: "data:image/jpeg;base64,AAA",
      caption: "Added beans",
    });
  });

  it("keeps only the most recent frame, never a list", () => {
    let s = applyFrame(initialRunState, frame("AAA", "one"));
    s = applyFrame(s, frame("BBB", "two"));
    s = applyFrame(s, frame("CCC", "three"));
    expect(s.browserFrame?.src).toBe("data:image/jpeg;base64,CCC");
    expect(s.browserFrame?.caption).toBe("three");
    // No array of frames accumulates anywhere on the state.
    expect(Array.isArray((s as unknown as { frames?: unknown }).frames)).toBe(false);
  });

  it("ignores an empty frame rather than blanking a good one", () => {
    const good = applyFrame(initialRunState, frame("AAA", "kept"));
    const after = applyFrame(good, frame("", "should be ignored"));
    expect(after.browserFrame?.src).toBe("data:image/jpeg;base64,AAA");
    expect(after.browserFrame?.caption).toBe("kept");
  });

  it("clears the frame when a new run starts", () => {
    const withFrame = applyFrame(initialRunState, frame("AAA", "old run"));
    expect(withFrame.browserFrame).not.toBeNull();
    const fresh = applyFrame(withFrame, {
      event: "run.started",
      data: { run_id: "r1", model: "sol" },
    });
    expect(fresh.browserFrame).toBeNull();
  });

  it("does not add the frame to the audit timeline", () => {
    const s = applyFrame(initialRunState, frame("AAA", "x"));
    expect(s.audit.some((e) => e.step === "browser.frame")).toBe(false);
  });
});
