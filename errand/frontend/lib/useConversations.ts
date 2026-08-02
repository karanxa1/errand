// useConversations — owns the sidebar's conversation list and its mutations.
//
// GET /api/conversations returns items newest-first. Creating (POST), deleting
// (DELETE), and patching (PATCH title/profile/model) all keep the local list in
// sync without a full refetch, and `refresh()` re-pulls after a turn so the
// updated_at ordering stays correct. Every request sends the Bearer token.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./config";
import type { Conversation } from "./useChat";
import type { ProfileKind } from "./types";

export interface ConversationsApi {
  conversations: Conversation[];
  loading: boolean;
  refresh: () => Promise<void>;
  create: (init?: {
    title?: string;
    profile?: ProfileKind;
    model?: string;
  }) => Promise<Conversation | null>;
  remove: (id: string) => Promise<void>;
  patch: (
    id: string,
    changes: { title?: string; profile?: ProfileKind; model?: string },
  ) => Promise<void>;
  // Local-only rename (used when the backend auto-titles over the stream).
  setTitle: (id: string, title: string) => void;
}

export function useConversations(token: string | null): ConversationsApi {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const tokenRef = useRef<string | null>(token);
  tokenRef.current = token;

  const authHeaders = useCallback(
    (json = false): HeadersInit => {
      const tk = tokenRef.current ?? "";
      return json
        ? { "Content-Type": "application/json", Authorization: `Bearer ${tk}` }
        : { Authorization: `Bearer ${tk}` };
    },
    [],
  );

  const refresh = useCallback(async () => {
    if (!tokenRef.current) {
      setConversations([]);
      setLoading(false);
      return;
    }
    try {
      const res = await fetch(api("/api/conversations"), {
        headers: authHeaders(),
      });
      if (!res.ok) return;
      setConversations((await res.json()) as Conversation[]);
    } catch {
      /* leave the current list in place on a transient failure */
    } finally {
      setLoading(false);
    }
  }, [authHeaders]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    (async () => {
      if (!token) {
        if (alive) {
          setConversations([]);
          setLoading(false);
        }
        return;
      }
      await refresh();
    })();
    return () => {
      alive = false;
    };
  }, [token, refresh]);

  const create = useCallback(
    async (init?: { title?: string; profile?: ProfileKind; model?: string }) => {
      if (!tokenRef.current) return null;
      try {
        const res = await fetch(api("/api/conversations"), {
          method: "POST",
          headers: authHeaders(true),
          body: JSON.stringify(init ?? {}),
        });
        if (!res.ok) return null;
        const conv = (await res.json()) as Conversation;
        setConversations((list) => [conv, ...list.filter((c) => c.id !== conv.id)]);
        return conv;
      } catch {
        return null;
      }
    },
    [authHeaders],
  );

  const remove = useCallback(
    async (id: string) => {
      // Optimistic: drop it now, restore on failure.
      const prev = conversations;
      setConversations((list) => list.filter((c) => c.id !== id));
      try {
        const res = await fetch(api(`/api/conversations/${id}`), {
          method: "DELETE",
          headers: authHeaders(),
        });
        if (!res.ok && res.status !== 204) setConversations(prev);
      } catch {
        setConversations(prev);
      }
    },
    [authHeaders, conversations],
  );

  const patch = useCallback(
    async (
      id: string,
      changes: { title?: string; profile?: ProfileKind; model?: string },
    ) => {
      setConversations((list) =>
        list.map((c) => (c.id === id ? { ...c, ...changes } : c)),
      );
      try {
        await fetch(api(`/api/conversations/${id}`), {
          method: "PATCH",
          headers: authHeaders(true),
          body: JSON.stringify(changes),
        });
      } catch {
        /* local state already updated; a later refresh reconciles */
      }
    },
    [authHeaders],
  );

  const setTitle = useCallback((id: string, title: string) => {
    setConversations((list) =>
      list.map((c) => (c.id === id ? { ...c, title } : c)),
    );
  }, []);

  return { conversations, loading, refresh, create, remove, patch, setTitle };
}
