// @vitest-environment jsdom

// McpPanel — the rendering guarantees that matter.
//
// Chiefly: CONTENT IS VISIBLE BY DEFAULT. Nothing in this sheet is gated behind an
// animation or a fetch completing, because a panel that renders empty when a
// reveal does not fire is indistinguishable from a broken panel.
//
// Also pinned: a credential is never echoed back into the DOM, and a control the
// deployment cannot honour is not offered.

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import McpPanel, { type McpCapabilities } from "./McpPanel";
import type { McpApi, McpServer } from "@/lib/useMcpServers";

const CAPS: McpCapabilities = {
  allowStdio: false,
  maxServers: 12,
  canStoreCredentials: true,
  canSignIn: true,
};

function server(overrides: Partial<McpServer> = {}): McpServer {
  return {
    id: "srv1",
    name: "Acme CRM",
    config: { url: "https://crm.example.com/mcp", transport: "http" },
    transport: "http",
    auth_mode: "none",
    enabled: true,
    status: "connected",
    error: null,
    header_names: [],
    authorized: false,
    tools: [
      {
        name: "find_customer",
        tool_id: "mcp__Acme-CRM__find_customer",
        description: "Look up a customer by email.",
      },
    ],
    tools_updated_at: "2026-08-03T00:00:00Z",
    created_at: "2026-08-03T00:00:00Z",
    ...overrides,
  };
}

function api(overrides: Partial<McpApi> = {}): McpApi {
  return {
    servers: [],
    loading: false,
    authorizing: {},
    refresh: vi.fn(async () => {}),
    create: vi.fn(async () => ({ ok: true, server: server() }) as never),
    update: vi.fn(async () => ({ ok: true })),
    remove: vi.fn(async () => ({ ok: true })),
    test: vi.fn(async () => ({ ok: true })),
    authorize: vi.fn(async () => ({ ok: true })),
    disconnect: vi.fn(async () => ({ ok: true })),
    ...overrides,
  };
}

describe("McpPanel", () => {
  it("renders its heading and the add control with no data at all", () => {
    // The empty state is a real state, not a spinner. A user with no servers must
    // still see what this panel is for and how to start.
    render(<McpPanel api={api()} capabilities={CAPS} onClose={() => {}} />);
    expect(screen.getByRole("heading", { name: /tool servers/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /add a server/i })).toBeTruthy();
    expect(screen.getByText(/no servers yet/i)).toBeTruthy();
    // And the count is honest rather than absent.
    expect(screen.getByText(/0 of 12/)).toBeTruthy();
  });

  it("shows a connected server, its target and its tool count without expanding", () => {
    render(
      <McpPanel api={api({ servers: [server()] })} capabilities={CAPS} onClose={() => {}} />,
    );
    expect(screen.getByText("Acme CRM")).toBeTruthy();
    expect(screen.getByText("https://crm.example.com/mcp")).toBeTruthy();
    // Singular, because there is exactly one tool.
    expect(screen.getByText("1 tool")).toBeTruthy();
  });

  it("offers Authorize only when the server actually needs it", () => {
    const { rerender } = render(
      <McpPanel api={api({ servers: [server()] })} capabilities={CAPS} onClose={() => {}} />,
    );
    expect(screen.queryByRole("button", { name: /^authorize$/i })).toBeNull();

    rerender(
      <McpPanel
        api={api({
          servers: [server({ status: "authorizing", auth_mode: "oauth" })],
        })}
        capabilities={CAPS}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /authorize/i })).toBeTruthy();
    expect(screen.getByText(/needs authorizing/i)).toBeTruthy();
  });

  it("says Waiting while an authorization popup is open", () => {
    // Otherwise the button invites a second click that would supersede the live
    // attempt the user is already completing.
    render(
      <McpPanel
        api={api({
          servers: [server({ status: "authorizing", auth_mode: "oauth" })],
          authorizing: { srv1: true },
        })}
        capabilities={CAPS}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /waiting/i })).toBeTruthy();
  });

  it("surfaces a connection error rather than a bare status word", () => {
    render(
      <McpPanel
        api={api({
          servers: [
            server({
              status: "error",
              error: "Could not reach the server. Check the URL.",
              tools: [],
            }),
          ],
        })}
        capabilities={CAPS}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(/not reachable/i)).toBeTruthy();
  });

  it("names configured credential headers but never their values", () => {
    // The value is not in the props at all — the API does not return it. This pins
    // that the panel does not invent a place to show one either.
    const { container } = render(
      <McpPanel
        api={api({
          servers: [
            server({ auth_mode: "headers", header_names: ["X-Api-Key"] }),
          ],
        })}
        capabilities={CAPS}
        onClose={() => {}}
      />,
    );
    expect(container.textContent).not.toMatch(/Bearer|sk-|secret/i);
  });

  it("disables credential auth modes when the deployment cannot store them", () => {
    // A control that can only fail is worse than an absent one. The modes stay
    // visible but unusable, with the reason stated — hiding them entirely would
    // leave a user wondering why a token option they have seen elsewhere is gone.
    render(
      <McpPanel
        api={api()}
        capabilities={{ ...CAPS, canStoreCredentials: false }}
        onClose={() => {}}
      />,
    );
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /add a server/i }));
    });

    const none = screen.getByRole("button", { name: "None" }) as HTMLButtonElement;
    const signIn = screen.getByRole("button", { name: "Sign in" }) as HTMLButtonElement;
    const token = screen.getByRole("button", { name: "Token" }) as HTMLButtonElement;
    expect(none.disabled).toBe(false);
    expect(signIn.disabled).toBe(true);
    expect(token.disabled).toBe(true);
    expect(screen.getByText(/cannot store credentials/i)).toBeTruthy();
  });

  it("disables Sign in when the backend has no public callback URL", () => {
    // Offering it would walk the user into a redirect the authorization server
    // refuses, with an error that names nothing to do with configuration. Token and
    // open servers still work, so only that one segment is disabled — and the note
    // describes the SEGMENT, not whichever mode happens to be selected.
    render(
      <McpPanel
        api={api()}
        capabilities={{ ...CAPS, canSignIn: false }}
        onClose={() => {}}
      />,
    );
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /add a server/i }));
    });

    expect((screen.getByRole("button", { name: "Sign in" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Token" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "None" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText(/MCP_OAUTH_REDIRECT_BASE/)).toBeTruthy();
    // The selected mode is still None, so its own hint must not be replaced.
    expect(screen.getByText(/for an open server/i)).toBeTruthy();
  });

  it("offers all three auth modes when credentials can be stored", () => {
    render(<McpPanel api={api()} capabilities={CAPS} onClose={() => {}} />);
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /add a server/i }));
    });
    for (const label of ["None", "Sign in", "Token"]) {
      const button = screen.getByRole("button", { name: label }) as HTMLButtonElement;
      expect(button.disabled).toBe(false);
    }
    // Defaults to None, which is the mode that needs no secret and which lazily
    // discovers OAuth from a 401.
    expect(screen.getByText(/for an open server/i)).toBeTruthy();
  });

  it("reveals the token fields only in Token mode, as a password input", () => {
    render(<McpPanel api={api()} capabilities={CAPS} onClose={() => {}} />);
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /add a server/i }));
    });
    expect(screen.queryByPlaceholderText(/^Bearer/)).toBeNull();

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Token" }));
    });
    const value = screen.getByPlaceholderText(/^Bearer/) as HTMLInputElement;
    // A credential: never autofilled, never rendered as readable text.
    expect(value.type).toBe("password");
    expect(value.autocomplete).toBe("off");
  });

  it("refuses to submit without a name or a URL, naming which is missing", async () => {
    const create = vi.fn(async () => ({ ok: true, server: server() }) as never);
    render(
      <McpPanel api={api({ create })} capabilities={CAPS} onClose={() => {}} />,
    );
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /add a server/i }));
    });

    // Empty: the name is called out first.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^add server$/i }));
    });
    expect(screen.getByRole("alert").textContent).toMatch(/name/i);
    expect(create).not.toHaveBeenCalled();

    // Named but no URL: the URL is called out.
    act(() => {
      fireEvent.change(screen.getByPlaceholderText("GitHub"), {
        target: { value: "Acme" },
      });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^add server$/i }));
    });
    expect(screen.getByRole("alert").textContent).toMatch(/URL/i);
    expect(create).not.toHaveBeenCalled();
  });

  it("reports what happened after a successful add", async () => {
    const create = vi.fn(
      async () =>
        ({
          ok: true,
          server: server({ status: "connected" }),
        }) as never,
    );
    render(
      <McpPanel api={api({ create })} capabilities={CAPS} onClose={() => {}} />,
    );
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /add a server/i }));
    });
    act(() => {
      fireEvent.change(screen.getByPlaceholderText("GitHub"), {
        target: { value: "Acme CRM" },
      });
      fireEvent.change(screen.getByPlaceholderText(/example\.com/), {
        target: { value: "https://crm.example.com/mcp" },
      });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^add server$/i }));
    });

    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Acme CRM",
        url: "https://crm.example.com/mcp",
        auth_mode: "none",
      }),
    );
    // A verdict, not just "saved": the tool count is the thing the user wants.
    expect(screen.getByRole("status").textContent).toMatch(/1 tool available/i);
  });

  it("shows the backend's refusal message when an add fails", async () => {
    const create = vi.fn(async () => ({
      ok: false as const,
      error: "Server host points at a non-public address (10.0.0.5).",
    }));
    render(
      <McpPanel api={api({ create })} capabilities={CAPS} onClose={() => {}} />,
    );
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /add a server/i }));
    });
    act(() => {
      fireEvent.change(screen.getByPlaceholderText("GitHub"), {
        target: { value: "Internal" },
      });
      fireEvent.change(screen.getByPlaceholderText(/example\.com/), {
        target: { value: "https://internal.example.com/mcp" },
      });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^add server$/i }));
    });
    // The form stays open with the reason, so the URL can be fixed in place.
    expect(screen.getByRole("alert").textContent).toMatch(/non-public/i);
    expect(screen.getByRole("button", { name: /^add server$/i })).toBeTruthy();
  });

  it("disables adding at the limit and says why", () => {
    const servers = Array.from({ length: 2 }, (_, i) =>
      server({ id: `s${i}`, name: `Server ${i}` }),
    );
    render(
      <McpPanel
        api={api({ servers })}
        capabilities={{ ...CAPS, maxServers: 2 }}
        onClose={() => {}}
      />,
    );
    const add = screen.getByRole("button", { name: /add a server/i }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    expect(screen.getByText(/limit reached/i)).toBeTruthy();
  });

  it("marks a disabled server as off and stops claiming its tools", () => {
    render(
      <McpPanel
        api={api({ servers: [server({ enabled: false })] })}
        capabilities={CAPS}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("Off")).toBeTruthy();
    expect(screen.queryByText("1 tool")).toBeNull();
  });

  it("is a labelled modal dialog with a working close control", () => {
    const onClose = vi.fn();
    render(<McpPanel api={api()} capabilities={CAPS} onClose={onClose} />);
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    within(dialog).getByRole("button", { name: /^close$/i }).click();
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<McpPanel api={api()} capabilities={CAPS} onClose={onClose} />);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("shows the namespaced tool id language the agent actually sees", () => {
    // Expanding a row reveals the tools. The user should be able to tell what the
    // agent was told, which is why the panel keeps the real tool names.
    render(
      <McpPanel api={api({ servers: [server()] })} capabilities={CAPS} onClose={() => {}} />,
    );
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /show details/i }));
    });
    expect(screen.getByText("find_customer")).toBeTruthy();
    expect(screen.getByText(/look up a customer by email/i)).toBeTruthy();
    // The id the model is given is shown too, so what the agent was told is
    // inspectable rather than implied.
    expect(screen.getByTitle(/mcp__Acme-CRM__find_customer/)).toBeTruthy();
  });
});
