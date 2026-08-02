// The live browser handoff must survive a bad frame and reset per run.
//
// browser.liveview hands the interactive Cloudflare live-view URL back to the
// human to log in / pay. The reducer keeps only the LATEST url (never a list),
// ignores an empty/missing url (so a stray frame can't wipe a good one), clears
// it when a new run starts, and never pushes an audit entry (mirroring
// browser.frame). This pins all four.

import { describe, expect, it } from "vitest";
import { applyFrame, initialRunState } from "./errandReducer";

function liveView(url: unknown) {
  return { event: "browser.liveview", data: { url } as Record<string, unknown> };
}

describe("browser.liveview", () => {
  it("stores the url in state.liveView", () => {
    const s = applyFrame(
      initialRunState,
      liveView("https://live.browser.run/s/abc"),
    );
    expect(s.liveView).toEqual({ url: "https://live.browser.run/s/abc" });
  });

  it("keeps only the most recent url, never a list", () => {
    let s = applyFrame(initialRunState, liveView("https://live.browser.run/one"));
    s = applyFrame(s, liveView("https://live.browser.run/two"));
    expect(s.liveView?.url).toBe("https://live.browser.run/two");
  });

  it("ignores an empty url rather than blanking a good one", () => {
    const good = applyFrame(
      initialRunState,
      liveView("https://live.browser.run/kept"),
    );
    const afterEmpty = applyFrame(good, liveView(""));
    expect(afterEmpty.liveView?.url).toBe("https://live.browser.run/kept");
    const afterMissing = applyFrame(afterEmpty, liveView(undefined));
    expect(afterMissing.liveView?.url).toBe("https://live.browser.run/kept");
  });

  it("clears the live view when a new run starts", () => {
    const withView = applyFrame(
      initialRunState,
      liveView("https://live.browser.run/old"),
    );
    expect(withView.liveView).not.toBeNull();
    const fresh = applyFrame(withView, {
      event: "run.started",
      data: { run_id: "r1", model: "sol" },
    });
    expect(fresh.liveView).toBeNull();
  });

  it("does not add the live view to the audit timeline", () => {
    const s = applyFrame(
      initialRunState,
      liveView("https://live.browser.run/x"),
    );
    expect(s.audit.some((e) => e.step === "browser.liveview")).toBe(false);
  });
});
