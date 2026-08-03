// useMcpServers — the user's own MCP servers and their authorization state.
//
// Mirrors useConversations: mutations keep the local list in sync WITHOUT a
// refetch, and `refresh()` is for the initial load and for recovering a failed
// mutation, not for after every change. Every request sends the Bearer token.
//
// The one place this needs more than useConversations is OAuth. Authorizing is a
// three-step exchange the browser has to drive:
//
//   1. POST .../authorize                 -> {attempt_id, authorization_url}
//   2. open that URL in a POPUP; the user signs in and consents
//   3. learn the outcome
//
// Step 3 has two paths on purpose. The callback page postMessages its opener,
// which is instant, and we ALSO poll GET .../authorize/{attempt_id}. The message
// alone is not reliable — a blocked popup never opens, and a browser may sever
// `window.opener` across a cross-origin navigation — so the poll is the path that
// always works and the message is what makes it feel immediate. better-chatbot
// uses the postMessage; the poll is the belt to its braces.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./config";

export type McpAuthMode = "none" | "headers" | "oauth";
export type McpStatus =
  | "unknown"
  | "connected"
  | "authorizing"
  | "error";

export interface McpToolInfo {
  name: string;
  /** The namespaced name the model actually sees, e.g. `mcp__GitHub__search`. */
  tool_id: string;
  description: string;
}

export interface McpServer {
  id: string;
  name: string;
  /** Redacted by the server: no secret headers, and stdio env keys only. */
  config: { url?: string; transport?: string; command?: string; args?: string[]; envKeys?: string[] };
  transport: string;
  auth_mode: McpAuthMode;
  enabled: boolean;
  status: McpStatus;
  error: string | null;
  /** Which credential headers are set. Names only — never values. */
  header_names: string[];
  authorized: boolean;
  tools: McpToolInfo[];
  tools_updated_at: string | null;
  created_at: string;
}

export interface McpDraft {
  name: string;
  url?: string;
  transport?: "http" | "sse";
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  auth_mode: McpAuthMode;
  headers?: Record<string, string>;
}

export interface McpApi {
  servers: McpServer[];
  loading: boolean;
  /** Set while an authorization popup is in flight, keyed by server id. */
  authorizing: Record<string, boolean>;
  refresh: () => Promise<void>;
  create: (draft: McpDraft) => Promise<{ ok: true; server: McpServer } | { ok: false; error: string }>;
  update: (
    id: string,
    changes: Partial<Pick<McpServer, "name" | "enabled" | "auth_mode">> & {
      headers?: Record<string, string>;
      config?: McpDraft["url"] extends never ? never : Record<string, unknown>;
    },
  ) => Promise<{ ok: boolean; error?: string }>;
  // Reports its outcome rather than returning void: the optimistic delete is rolled
  // back on failure, and a caller that announced "Removed X" unconditionally would
  // leave that message on screen next to the restored row.
  remove: (id: string) => Promise<{ ok: boolean; error?: string }>;
  test: (id: string) => Promise<{ ok: boolean; error?: string }>;
  authorize: (id: string) => Promise<{ ok: boolean; error?: string }>;
  disconnect: (id: string) => Promise<{ ok: boolean; error?: string }>;
}

// How often the authorization poll asks. A human signing in takes seconds at
// best, so a tighter interval is just load; 1.5s is the same order as the spend
// gate's poll and feels immediate next to a browser round-trip.
const AUTH_POLL_MS = 1500;
// Give up after this. Matches the backend's AUTH_TIMEOUT_S (300s) so the UI never
// claims an attempt is still live after the server has abandoned it.
const AUTH_POLL_TIMEOUT_MS = 300_000;

// How long to keep polling after the popup closes before calling it cancelled.
//
// A closed popup is ambiguous: it is what cancelling looks like AND what success
// looks like, because the callback page closes itself once it has handed over the
// code. Everything expensive — the token exchange, the MCP connect, tools/list —
// runs server-side after that. This window has to cover all of it, so it is sized
// against the connect budget (CONNECT_TIMEOUT_S is 30s server-side) rather than
// against how fast a person clicks.
const POPUP_CLOSED_GRACE_MS = 45_000;

async function readError(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) {
      // FastAPI validation errors arrive as a list of objects.
      const first = detail[0] as { msg?: string };
      if (first?.msg) return first.msg;
    }
  } catch {
    /* non-JSON body */
  }
  return fallback;
}

function draftToBody(draft: McpDraft) {
  const config: Record<string, unknown> = draft.command
    ? { command: draft.command, args: draft.args ?? [], env: draft.env ?? {} }
    : { url: draft.url ?? "", transport: draft.transport ?? "http" };
  return {
    name: draft.name,
    config,
    auth_mode: draft.auth_mode,
    ...(draft.auth_mode === "headers" ? { headers: draft.headers ?? {} } : {}),
  };
}

export function useMcpServers(token: string | null): McpApi {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [authorizing, setAuthorizing] = useState<Record<string, boolean>>({});
  const tokenRef = useRef<string | null>(token);
  tokenRef.current = token;
  // The current list, readable from a callback without becoming a dependency.
  // `remove` needs the row it is about to drop BEFORE it drops it, and reading
  // that from inside a setState updater is not safe: React does not promise the
  // updater has run by the time the await after it resolves, so the restore would
  // see an undefined row and silently keep the deletion. Same reason
  // useConversations keeps a listRef.
  const listRef = useRef<McpServer[]>(servers);
  listRef.current = servers;
  // Every popup and poll timer we own, so unmounting cannot leave either running.
  const cleanups = useRef<Array<() => void>>([]);

  useEffect(
    () => () => {
      cleanups.current.forEach((fn) => fn());
      cleanups.current = [];
    },
    [],
  );

  const authHeaders = useCallback((json = false): HeadersInit => {
    const tk = tokenRef.current ?? "";
    return json
      ? { "Content-Type": "application/json", Authorization: `Bearer ${tk}` }
      : { Authorization: `Bearer ${tk}` };
  }, []);

  const refresh = useCallback(async () => {
    if (!tokenRef.current) {
      setServers([]);
      setLoading(false);
      return;
    }
    try {
      const res = await fetch(api("/api/mcp/servers"), { headers: authHeaders() });
      // 404 means the feature is switched off on this deployment. An empty list is
      // the honest render for that, and the panel is not offered anyway.
      if (!res.ok) return;
      setServers((await res.json()) as McpServer[]);
    } catch {
      /* leave the current list in place on a transient failure */
    } finally {
      setLoading(false);
    }
  }, [authHeaders]);

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, [token, refresh]);

  const replace = useCallback((server: McpServer) => {
    setServers((list) => {
      const index = list.findIndex((s) => s.id === server.id);
      if (index === -1) return [...list, server];
      const next = [...list];
      next[index] = server;
      return next;
    });
  }, []);

  const create = useCallback<McpApi["create"]>(
    async (draft) => {
      try {
        const res = await fetch(api("/api/mcp/servers"), {
          method: "POST",
          headers: authHeaders(true),
          body: JSON.stringify(draftToBody(draft)),
        });
        if (!res.ok) {
          return { ok: false, error: await readError(res, "Could not add that server.") };
        }
        const server = (await res.json()) as McpServer;
        setServers((list) => [...list.filter((s) => s.id !== server.id), server]);
        return { ok: true, server };
      } catch {
        return { ok: false, error: "Could not reach the backend." };
      }
    },
    [authHeaders],
  );

  const update = useCallback<McpApi["update"]>(
    async (id, changes) => {
      try {
        const res = await fetch(api(`/api/mcp/servers/${id}`), {
          method: "PATCH",
          headers: authHeaders(true),
          body: JSON.stringify(changes),
        });
        if (!res.ok) {
          return { ok: false, error: await readError(res, "Could not save that change.") };
        }
        replace((await res.json()) as McpServer);
        return { ok: true };
      } catch {
        return { ok: false, error: "Could not reach the backend." };
      }
    },
    [authHeaders, replace],
  );

  const remove = useCallback(
    async (id: string) => {
      // Optimistic, restoring at the original index on failure — same shape as
      // useConversations.remove, and for the same reason. The row is read from the
      // ref, not from inside the updater, so it is definitely available later.
      const index = listRef.current.findIndex((s) => s.id === id);
      if (index === -1) return { ok: true };
      const row = listRef.current[index];

      setServers((list) => list.filter((s) => s.id !== id));

      // A functional update, so a concurrent change to another row is merged
      // rather than overwritten by a stale snapshot.
      const restore = () =>
        setServers((list) => {
          if (list.some((s) => s.id === id)) return list;
          const next = [...list];
          next.splice(Math.min(index, next.length), 0, row);
          return next;
        });

      try {
        const res = await fetch(api(`/api/mcp/servers/${id}`), {
          method: "DELETE",
          headers: authHeaders(),
        });
        // 404 means it is already gone server-side, so the local drop was correct.
        // Anything else that failed means the row still exists and must come back,
        // or the panel would show a server as removed while the agent can still
        // call its tools.
        if (!res.ok && res.status !== 204 && res.status !== 404) {
          restore();
          return { ok: false, error: await readError(res, "Could not remove that server.") };
        }
        return { ok: true };
      } catch {
        restore();
        return { ok: false, error: "Could not reach the backend." };
      }
    },
    [authHeaders],
  );

  const test = useCallback<McpApi["test"]>(
    async (id) => {
      try {
        const res = await fetch(api(`/api/mcp/servers/${id}/refresh`), {
          method: "POST",
          headers: authHeaders(),
        });
        if (!res.ok) {
          return { ok: false, error: await readError(res, "Could not test that server.") };
        }
        const server = (await res.json()) as McpServer;
        replace(server);
        return server.status === "connected"
          ? { ok: true }
          : { ok: false, error: server.error ?? "The server needs authorizing." };
      } catch {
        return { ok: false, error: "Could not reach the backend." };
      }
    },
    [authHeaders, replace],
  );

  const disconnect = useCallback<McpApi["disconnect"]>(
    async (id) => {
      try {
        const res = await fetch(api(`/api/mcp/servers/${id}/disconnect`), {
          method: "POST",
          headers: authHeaders(),
        });
        if (!res.ok) {
          return { ok: false, error: await readError(res, "Could not disconnect.") };
        }
        replace((await res.json()) as McpServer);
        return { ok: true };
      } catch {
        return { ok: false, error: "Could not reach the backend." };
      }
    },
    [authHeaders, replace],
  );

  const authorize = useCallback<McpApi["authorize"]>(
    async (id) => {
      setAuthorizing((state) => ({ ...state, [id]: true }));
      const done = () => setAuthorizing((state) => ({ ...state, [id]: false }));

      let attemptId: string;
      let authorizationUrl: string;
      try {
        const res = await fetch(api(`/api/mcp/servers/${id}/authorize`), {
          method: "POST",
          headers: authHeaders(),
        });
        if (!res.ok) {
          done();
          return { ok: false, error: await readError(res, "Could not start authorization.") };
        }
        const body = (await res.json()) as {
          attempt_id: string;
          authorization_url: string;
        };
        attemptId = body.attempt_id;
        authorizationUrl = body.authorization_url;
      } catch {
        done();
        return { ok: false, error: "Could not reach the backend." };
      }

      // A popup rather than a redirect, so the app — and any in-flight
      // conversation — is never navigated away from.
      const popup = window.open(
        authorizationUrl,
        "errand-mcp-oauth",
        "width=520,height=680,noopener=no",
      );

      return new Promise<{ ok: boolean; error?: string }>((resolve) => {
        let settled = false;
        const startedAt = Date.now();
        // When the popup was first observed closed, so the grace period below can be
        // measured from it rather than from the start of the flow.
        let closedAt: number | null = null;

        const finish = (result: { ok: boolean; error?: string }) => {
          if (settled) return;
          settled = true;
          window.clearInterval(timer);
          window.removeEventListener("message", onMessage);
          cleanups.current = cleanups.current.filter((fn) => fn !== teardown);
          done();
          resolve(result);
        };

        const onMessage = (event: MessageEvent) => {
          // The callback page's postMessage. Only used as a nudge to poll
          // immediately: the poll is authoritative, so this deliberately does not
          // trust the message's contents, which sidesteps having to validate an
          // origin that legitimately differs from the app's.
          const data = event.data as { type?: string } | null;
          if (data?.type === "MCP_OAUTH_SUCCESS" || data?.type === "MCP_OAUTH_ERROR") {
            void poll();
          }
        };

        const poll = async () => {
          if (settled) return;
          try {
            const res = await fetch(
              api(`/api/mcp/servers/${id}/authorize/${attemptId}`),
              { headers: authHeaders() },
            );
            if (!res.ok) return;
            const body = (await res.json()) as {
              state: "pending" | "connected" | "error" | "expired";
              error: string | null;
              server: McpServer | null;
            };
            if (body.server) replace(body.server);
            if (body.state === "connected") finish({ ok: true });
            else if (body.state === "error" || body.state === "expired") {
              finish({ ok: false, error: body.error ?? "Authorization failed." });
            }
          } catch {
            /* transient: the next tick tries again */
          }
        };

        const timer = window.setInterval(() => {
          if (Date.now() - startedAt > AUTH_POLL_TIMEOUT_MS) {
            finish({ ok: false, error: "Authorization timed out." });
            return;
          }
          // A closed popup MIGHT mean the user gave up — but it is also exactly what
          // a SUCCESSFUL authorization looks like, because the callback page closes
          // itself ~1.2s after delivering the code. The token exchange, the MCP
          // connect and tool discovery all happen server-side AFTER that, so the
          // very next poll legitimately still reads `pending`.
          //
          // Settling on the first pending poll after a close therefore reported a
          // normal, slightly slow success as "cancelled" and left the row stuck in
          // authorizing. So a close only starts a grace period: keep polling, and
          // only give up if it stays pending through it.
          if (popup && popup.closed) {
            if (closedAt === null) closedAt = Date.now();
            void poll();
            if (Date.now() - closedAt > POPUP_CLOSED_GRACE_MS) {
              finish({ ok: false, error: "Authorization was cancelled." });
            }
            return;
          }
          void poll();
        }, AUTH_POLL_MS);

        const teardown = () => {
          window.clearInterval(timer);
          window.removeEventListener("message", onMessage);
          try {
            popup?.close();
          } catch {
            /* already gone */
          }
        };
        cleanups.current.push(teardown);
        window.addEventListener("message", onMessage);

        if (!popup) {
          // Popup blocked. The flow is still live server-side, so keep polling and
          // tell the user where to go — losing the attempt would be worse.
          void poll();
        }
      });
    },
    [authHeaders, replace],
  );

  return {
    servers,
    loading,
    authorizing,
    refresh,
    create,
    update,
    remove,
    test,
    authorize,
    disconnect,
  };
}
