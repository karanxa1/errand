// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mirrorSessionToken } from "./sessionMirror";

describe("mirrorSessionToken", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("POSTs a token to the same-origin session route", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true }) as Response);
    vi.stubGlobal("fetch", fetchMock);
    await expect(mirrorSessionToken("jwt-value")).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledWith("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: "jwt-value" }),
    });
  });

  it("DELETEs the mirror on logout", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true }) as Response);
    vi.stubGlobal("fetch", fetchMock);
    await expect(mirrorSessionToken(null)).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledWith("/api/session", { method: "DELETE" });
  });

  it("reports failure instead of throwing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      }),
    );
    await expect(mirrorSessionToken("jwt-value")).resolves.toBe(false);
  });
});
