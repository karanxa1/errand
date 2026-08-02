// @vitest-environment jsdom

// useMcpServers — the OAuth hand-off and the failure paths that would otherwise
// leave the UI lying about state.
//
// The authorization flow is the part worth testing: it spans a POST, a popup, a
// postMessage that may never arrive, and a poll. The properties pinned here are
// the ones whose absence produces a stuck spinner or a false "connected":
//
//   * the poll is what settles the flow, not the postMessage;
//   * a blocked popup still completes, because the flow is live server-side;
//   * closing the popup after consenting reads as success, not as cancellation;
//   * a server-side error surfaces instead of spinning forever.

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useMcpServers } from "./useMcpServers";

const SERVER = {
  id: "srv1",
  name: "GitHub",
  config: { url: "https://example.com/mcp", transport: "http" },
  transport: "http",
  auth_mode: "oauth" as const,
  enabled: true,
  status: "authorizing" as const,
  error: null,
  header_names: [],
  authorized: false,
  tools: [],
  tools_updated_at: null,
  created_at: "2026-08-03T00:00:00Z",
};

const CONNECTED = {
  ...SERVER,
  status: "connected" as const,
  authorized: true,
  tools: [{ name: "search", tool_id: "mcp__GitHub__search", description: "Search." }],
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

let openedUrls: string[] = [];
let popup: { closed: boolean; close: () => void } | null;

beforeEach(() => {
  openedUrls = [];
  popup = { closed: false, close: () => {} };
  vi.stubGlobal(
    "open",
    vi.fn((url: string) => {
      openedUrls.push(url);
      return popup;
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("authorize", () => {
  it("opens the authorization URL and settles from the poll", async () => {
    // Deliberately NO postMessage: the poll alone must be able to finish the
    // flow, because window.opener is not reliably reachable from the popup.
    let polls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/servers")) return jsonResponse([SERVER]);
      if (url.endsWith("/authorize")) {
        return jsonResponse({
          attempt_id: "att1",
          authorization_url: "https://idp.example/authorize?state=abc",
        });
      }
      if (url.includes("/authorize/att1")) {
        polls += 1;
        return jsonResponse(
          polls < 2
            ? { state: "pending", error: null, server: SERVER }
            : { state: "connected", error: null, server: CONNECTED },
        );
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMcpServers("tok"));
    await waitFor(() => expect(result.current.servers).toHaveLength(1));

    let outcome: { ok: boolean; error?: string } | undefined;
    await act(async () => {
      const promise = result.current.authorize("srv1").then((r) => {
        outcome = r;
      });
      // Two poll ticks: the first is pending, the second connects.
      await vi.waitFor(() => expect(polls).toBeGreaterThanOrEqual(2), {
        timeout: 8000,
      });
      await promise;
    });

    expect(openedUrls).toEqual(["https://idp.example/authorize?state=abc"]);
    expect(outcome).toEqual({ ok: true });
    // The row is replaced from the poll's payload, so the UI shows the tools
    // without a separate refresh.
    expect(result.current.servers[0].status).toBe("connected");
    expect(result.current.servers[0].tools).toHaveLength(1);
    // And the per-server busy flag is cleared, or the button stays disabled.
    expect(result.current.authorizing.srv1).toBe(false);
  }, 20000);

  it("still completes when the popup is blocked", async () => {
    // A blocked popup does not cancel anything server-side: the flow is parked and
    // the user can complete it in another tab. Failing here would abandon a live
    // authorization.
    popup = null;
    let polls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/servers")) return jsonResponse([SERVER]);
      if (url.endsWith("/authorize")) {
        return jsonResponse({
          attempt_id: "att1",
          authorization_url: "https://idp.example/authorize?state=abc",
        });
      }
      if (url.includes("/authorize/att1")) {
        polls += 1;
        return jsonResponse({ state: "connected", error: null, server: CONNECTED });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMcpServers("tok"));
    await waitFor(() => expect(result.current.servers).toHaveLength(1));

    let outcome: { ok: boolean; error?: string } | undefined;
    await act(async () => {
      outcome = await result.current.authorize("srv1");
    });

    expect(polls).toBeGreaterThanOrEqual(1);
    expect(outcome).toEqual({ ok: true });
  }, 20000);

  it("reads a closed popup as success when the server says connected", async () => {
    // The callback page closes ITSELF on success, so "popup closed" arrives on the
    // happy path too. Treating it as cancellation would report failure for a
    // successful authorization — the poll has to get the final word.
    let polls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/servers")) return jsonResponse([SERVER]);
      if (url.endsWith("/authorize")) {
        return jsonResponse({
          attempt_id: "att1",
          authorization_url: "https://idp.example/authorize",
        });
      }
      if (url.includes("/authorize/att1")) {
        polls += 1;
        return jsonResponse({ state: "connected", error: null, server: CONNECTED });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMcpServers("tok"));
    await waitFor(() => expect(result.current.servers).toHaveLength(1));

    if (popup) popup.closed = true; // as the callback page leaves it

    let outcome: { ok: boolean; error?: string } | undefined;
    await act(async () => {
      outcome = await result.current.authorize("srv1");
    });

    expect(outcome?.ok).toBe(true);
    expect(polls).toBeGreaterThanOrEqual(1);
  }, 20000);

  it("surfaces a server-side authorization error instead of spinning", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/servers")) return jsonResponse([SERVER]);
      if (url.endsWith("/authorize")) {
        return jsonResponse({
          attempt_id: "att1",
          authorization_url: "https://idp.example/authorize",
        });
      }
      if (url.includes("/authorize/att1")) {
        return jsonResponse({
          state: "error",
          error: "The server denied authorization: access_denied",
          server: { ...SERVER, status: "error", error: "denied" },
        });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMcpServers("tok"));
    await waitFor(() => expect(result.current.servers).toHaveLength(1));

    let outcome: { ok: boolean; error?: string } | undefined;
    await act(async () => {
      outcome = await result.current.authorize("srv1");
    });

    expect(outcome?.ok).toBe(false);
    expect(outcome?.error).toContain("access_denied");
    expect(result.current.authorizing.srv1).toBe(false);
  }, 20000);

  it("reports the backend's message when authorization cannot start", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/servers")) return jsonResponse([SERVER]);
      if (url.endsWith("/authorize")) {
        return jsonResponse(
          { detail: "This deployment cannot store credentials." },
          400,
        );
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMcpServers("tok"));
    await waitFor(() => expect(result.current.servers).toHaveLength(1));

    let outcome: { ok: boolean; error?: string } | undefined;
    await act(async () => {
      outcome = await result.current.authorize("srv1");
    });

    expect(outcome).toEqual({
      ok: false,
      error: "This deployment cannot store credentials.",
    });
    // No popup for a flow that never started.
    expect(openedUrls).toEqual([]);
  });
});

describe("mutations", () => {
  it("sends a header credential under `headers`, never inside the config", async () => {
    // The config column is plain JSON; secrets go to the encrypted column. A
    // client that nested the token in `config` would defeat that.
    const bodies: unknown[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/servers") && init?.method === "POST") {
        bodies.push(JSON.parse(String(init.body)));
        return jsonResponse(CONNECTED, 201);
      }
      if (url.endsWith("/api/mcp/servers")) return jsonResponse([]);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMcpServers("tok"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.create({
        name: "GitHub",
        url: "https://example.com/mcp",
        transport: "http",
        auth_mode: "headers",
        headers: { Authorization: "Bearer secret" },
      });
    });

    expect(bodies).toHaveLength(1);
    const body = bodies[0] as { config: Record<string, unknown>; headers?: unknown };
    expect(body.config).toEqual({ url: "https://example.com/mcp", transport: "http" });
    expect(body.headers).toEqual({ Authorization: "Bearer secret" });
    expect(JSON.stringify(body.config)).not.toContain("secret");
  });

  it("restores a removed server when the delete fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/servers")) return jsonResponse([CONNECTED]);
      if (init?.method === "DELETE") return jsonResponse({ detail: "nope" }, 500);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMcpServers("tok"));
    await waitFor(() => expect(result.current.servers).toHaveLength(1));

    await act(async () => {
      await result.current.remove("srv1");
    });

    // Put back, because the row still exists server-side. Dropping it would show
    // a server as gone while the agent could still call its tools.
    expect(result.current.servers.map((s) => s.id)).toEqual(["srv1"]);
  });

  it("keeps a removed server dropped when the backend confirms", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/mcp/servers")) return jsonResponse([CONNECTED]);
      if (init?.method === "DELETE") return { ok: true, status: 204 } as Response;
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMcpServers("tok"));
    await waitFor(() => expect(result.current.servers).toHaveLength(1));

    await act(async () => {
      await result.current.remove("srv1");
    });

    expect(result.current.servers).toEqual([]);
  });

  it("reports a test that connects, and one that needs authorizing", async () => {
    let nextServer: unknown = CONNECTED;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/refresh")) return jsonResponse(nextServer);
      if (url.endsWith("/api/mcp/servers")) return jsonResponse([SERVER]);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMcpServers("tok"));
    await waitFor(() => expect(result.current.servers).toHaveLength(1));

    await act(async () => {
      expect(await result.current.test("srv1")).toEqual({ ok: true });
    });

    // A row that comes back still needing auth is NOT a successful test — saying
    // "reachable" there would be a false green.
    nextServer = { ...SERVER, status: "authorizing", error: null };
    await act(async () => {
      const outcome = await result.current.test("srv1");
      expect(outcome.ok).toBe(false);
      expect(outcome.error).toContain("authoriz");
    });
  });

  it("signs out without removing the server", async () => {
    const signedOut = { ...SERVER, authorized: false, status: "authorizing" as const };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/disconnect")) return jsonResponse(signedOut);
      if (url.endsWith("/api/mcp/servers")) return jsonResponse([CONNECTED]);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMcpServers("tok"));
    await waitFor(() => expect(result.current.servers).toHaveLength(1));

    await act(async () => {
      expect(await result.current.disconnect("srv1")).toEqual({ ok: true });
    });

    expect(result.current.servers).toHaveLength(1);
    expect(result.current.servers[0].authorized).toBe(false);
  });

  it("holds the list when the backend is unreachable", async () => {
    // A transient failure must not blank the panel.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([CONNECTED]))
      .mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMcpServers("tok"));
    await waitFor(() => expect(result.current.servers).toHaveLength(1));

    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.servers).toHaveLength(1);
  });

  it("clears the list when there is no token", async () => {
    const fetchMock = vi.fn(async () => jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useMcpServers(null));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.servers).toEqual([]);
    // No request at all without a token.
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
