import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchConversationSeed } from "./serverConversation";

describe("fetchConversationSeed", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns null without a token", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchConversationSeed("a".repeat(32), undefined)).resolves.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns an authenticated conversation", async () => {
    const detail = { id: "a".repeat(32), title: "Office", profile: "business", model: "sol", created_at: "x", updated_at: "x", messages: [] };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => detail }) as Response));
    await expect(fetchConversationSeed(detail.id, "jwt-value")).resolves.toEqual(detail);
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining(`/api/conversations/${detail.id}`), {
      headers: { Authorization: "Bearer jwt-value" },
      cache: "no-store",
    });
  });

  it("returns null for upstream rejection", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false }) as Response));
    await expect(fetchConversationSeed("a".repeat(32), "jwt-value")).resolves.toBeNull();
  });
});
