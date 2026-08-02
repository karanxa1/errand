// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useChat, type ConversationDetail } from "./useChat";

it("renders a matching server seed while the browser token hydrates", () => {
  const detail: ConversationDetail = {
    id: "a".repeat(32), title: "Office", profile: "business", model: "sol",
    created_at: "x", updated_at: "x",
    messages: [{ id: "m1", role: "assistant", content: "Seeded", created_at: "x" }],
  };
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  const { result } = renderHook(() => useChat({ conversationId: detail.id, token: null, initialDetail: detail }));
  expect(result.current.messages).toEqual(detail.messages);
  expect(fetchMock).not.toHaveBeenCalled();
});
