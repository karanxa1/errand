// useConversations — owns the sidebar's conversation list and its mutations.
//
// GET /api/conversations returns items newest-first. Every mutation keeps the
// local list in sync WITHOUT a refetch: creating, deleting, patching
// (title/profile/model), `insert()` for a chat the client has just materialized,
// and `bump()` for the reordering a finished turn causes. `refresh()` exists for
// the initial load and for recovering from a failed mutation — it is deliberately
// not called after every turn, because re-pulling the whole list to learn an
// ordering we already know is a round-trip for nothing.
//
// Every request sends the Bearer token.

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
  // Local-only insert for a conversation the client has just given an id and is
  // already streaming into. The row is created server-side on that same turn, so
  // this is what puts it in the rail immediately instead of a beat later.
  insert: (conv: Conversation) => void;
  // Local-only reorder: a finished turn moves its conversation to the top, which
  // is exactly what the server's updated_at ordering will say on the next load.
  bump: (id: string) => void;
  // Is this id already a row we know about? The caller uses it to avoid PATCHing
  // a conversation that has not been materialized server-side yet (that would
  // 404), and to decide whether a send needs to insert a rail entry.
  has: (id: string) => boolean;
}

export function useConversations(token: string | null): ConversationsApi {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const tokenRef = useRef<string | null>(token);
  tokenRef.current = token;
  // The current list, readable from a callback without becoming a dependency.
  const listRef = useRef<Conversation[]>(conversations);
  listRef.current = conversations;

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
      // Optimistic: drop it now, put it back at its original index on failure.
      //
      // The row is read from `listRef`, not from a captured `conversations`.
      // Closing over the state would put it in the dep array and churn this
      // callback's identity on every list change, and the restore would write
      // back a snapshot taken before any concurrent title/bump, silently
      // reverting those. The ref is always the current list, and the restore is a
      // functional update, so it merges instead of overwriting.
      const index = listRef.current.findIndex((c) => c.id === id);
      if (index === -1) return;
      const row = listRef.current[index];

      setConversations((list) => list.filter((c) => c.id !== id));

      const restore = () =>
        setConversations((list) => {
          if (list.some((c) => c.id === id)) return list;
          const next = [...list];
          next.splice(Math.min(index, next.length), 0, row);
          return next;
        });

      try {
        const res = await fetch(api(`/api/conversations/${id}`), {
          method: "DELETE",
          headers: authHeaders(),
        });
        // 404 means it was never materialized server-side (a new chat the
        // operator abandoned before sending). Dropping it locally is the whole
        // job — restoring it would resurrect a row that does not exist.
        if (!res.ok && res.status !== 204 && res.status !== 404) restore();
      } catch {
        restore();
      }
    },
    [authHeaders],
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

  const insert = useCallback((conv: Conversation) => {
    setConversations((list) =>
      list.some((c) => c.id === conv.id)
        ? list
        : [conv, ...list],
    );
  }, []);

  const bump = useCallback((id: string) => {
    setConversations((list) => {
      const index = list.findIndex((c) => c.id === id);
      // Already at the top: return the same array so React skips the re-render.
      if (index <= 0) return list;
      const moved = { ...list[index], updated_at: new Date().toISOString() };
      const next = [...list];
      next.splice(index, 1);
      return [moved, ...next];
    });
  }, []);

  const has = useCallback(
    (id: string) => listRef.current.some((c) => c.id === id),
    [],
  );

  return {
    conversations,
    loading,
    refresh,
    create,
    remove,
    patch,
    setTitle,
    insert,
    bump,
    has,
  };
}
