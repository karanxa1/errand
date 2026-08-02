import { api } from "./config";
import type { ConversationDetail } from "./useChat";

export async function fetchConversationSeed(id: string, token: string | undefined) {
  if (!token || !/^[0-9a-f]{32}$/.test(id)) return null;
  try {
    const response = await fetch(api(`/api/conversations/${id}`), {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    return response.ok ? ((await response.json()) as ConversationDetail) : null;
  } catch {
    return null;
  }
}
