"use client";

/* McpPanel — the user's tool servers.
 *
 * A sheet over the app rather than a route, so opening it never unmounts a
 * conversation or kills an in-flight stream (the same reason a first turn uses
 * pushState instead of router.push).
 *
 * On-brand and deliberately plain: tonal surfaces, self-coloured lips
 * (inset 1px of --color-edge) instead of drawn borders, bare marks with no tiles,
 * status shown as a drawn mark plus a word rather than a coloured chip. Content is
 * visible on mount — nothing here is gated behind an animation.
 */

import { useEffect, useId, useRef, useState } from "react";

import type { McpApi, McpAuthMode, McpServer } from "@/lib/useMcpServers";
import "./mcp.anim.css";
import {
  AlertMark,
  ChevronMark,
  CloseMark,
  ConnectedMark,
  IdleMark,
  KeyMark,
  RetryMark,
  ServerMark,
  ToolMark,
  TrashMark,
} from "./McpMarks";

export interface McpCapabilities {
  allowStdio: boolean;
  maxServers: number;
  canStoreCredentials: boolean;
  // False when the backend has no publicly reachable OAuth redirect base, so a
  // sign-in would end at a redirect the authorization server refuses. Offering the
  // option anyway would be a control that can only fail.
  canSignIn: boolean;
}

// One tonal step per state. Not a saturated swatch each: connected is the accent,
// everything else is a quiet tone off the surface, which is what keeps the list
// from reading as a row of status pills.
const STATE_TONE: Record<string, string> = {
  connected: "text-green",
  authorizing: "text-brass",
  error: "text-danger",
  unknown: "text-low",
};

function stateWord(server: McpServer): string {
  if (!server.enabled) return "Off";
  switch (server.status) {
    case "connected":
      return `${server.tools.length} tool${server.tools.length === 1 ? "" : "s"}`;
    case "authorizing":
      return "Needs authorizing";
    case "error":
      return "Not reachable";
    default:
      return "Not tested";
  }
}

function StateMark({ server }: { server: McpServer }) {
  if (!server.enabled) return <IdleMark />;
  switch (server.status) {
    case "connected":
      return <ConnectedMark />;
    case "authorizing":
      return <KeyMark />;
    case "error":
      return <AlertMark />;
    default:
      return <IdleMark />;
  }
}

export default function McpPanel({
  api,
  capabilities,
  onClose,
}: {
  api: McpApi;
  capabilities: McpCapabilities;
  onClose: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  // Escape closes. Focus lands on the close control so the sheet is keyboard
  // navigable from the moment it opens.
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const atLimit = api.servers.length >= capabilities.maxServers;

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-end"
      role="dialog"
      aria-modal="true"
      aria-label="Tool servers"
    >
      <button
        className="absolute inset-0 border-none bg-[rgba(4,8,6,0.62)]"
        aria-label="Close tool servers"
        onClick={onClose}
      />

      <section className="relative flex h-full w-[min(560px,100vw)] min-h-0 flex-col bg-ink-050 shadow-[inset_1px_0_0_var(--color-edge),-24px_0_60px_-30px_rgba(4,8,6,0.9)]">
        <header className="flex flex-none items-start gap-3 px-6 pt-6 pb-4">
          <span className="mt-[1px] inline-flex text-green">
            <ServerMark size={20} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="m-0 font-display text-[21px] leading-[1.2] text-hi">
              Tool servers
            </h2>
            <p className="mt-[3px] mb-0 text-[12.5px] leading-[1.5] text-mid">
              Connect an MCP server and its tools become available to the agent, in
              chat and on a call.
            </p>
          </div>
          <button
            ref={closeRef}
            className="-mr-1 -mt-1 inline-flex h-8 w-8 flex-none items-center justify-center rounded-[9px] border-none bg-transparent text-low transition-[background-color,color] duration-[160ms] ease-[ease] hover:bg-ink-150 hover:text-hi"
            onClick={onClose}
            aria-label="Close"
            type="button"
          >
            <CloseMark />
          </button>
        </header>

        {notice && (
          <p
            className="mx-6 mb-3 flex-none rounded-[10px] bg-ink-150 px-3 py-2 text-[12.5px] leading-[1.5] text-body shadow-[inset_0_0_0_1px_var(--color-edge)]"
            role="status"
          >
            {notice}
          </p>
        )}

        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-6 pb-4 [scrollbar-color:var(--color-ink-250)_transparent] [scrollbar-width:thin]">
          {api.loading && api.servers.length === 0 ? (
            <p className="m-0 py-3 text-[13px] text-low">Loading your servers…</p>
          ) : api.servers.length === 0 ? (
            <div className="py-3">
              <p className="m-0 text-[13px] leading-[1.6] text-mid">
                No servers yet. Add one and the agent can use its tools straight
                away.
              </p>
            </div>
          ) : (
            api.servers.map((server) => (
              <ServerRow
                key={server.id}
                server={server}
                api={api}
                busy={Boolean(api.authorizing[server.id])}
                onNotice={setNotice}
              />
            ))
          )}
        </div>

        <footer className="flex-none px-6 pt-4 pb-6 shadow-[inset_0_1px_0_var(--color-edge)]">
          {adding ? (
            <AddServerForm
              api={api}
              capabilities={capabilities}
              onDone={(message) => {
                setAdding(false);
                setNotice(message ?? null);
              }}
            />
          ) : (
            <div className="flex items-center gap-3">
              <button
                className="inline-flex h-[38px] items-center gap-2 rounded-chip border-none bg-green px-4 text-[13.5px] [font-weight:640] text-on-accent shadow-[inset_0_1px_0_rgba(255,255,255,0.2)] transition-[background-color] duration-[180ms] ease-[ease] hover:bg-green-soft disabled:cursor-default disabled:opacity-50"
                onClick={() => {
                  setNotice(null);
                  setAdding(true);
                }}
                disabled={atLimit}
                type="button"
              >
                Add a server
              </button>
              <span className="text-[11.5px] text-low">
                {atLimit
                  ? `Limit reached (${capabilities.maxServers}). Remove one to add another.`
                  : `${api.servers.length} of ${capabilities.maxServers}`}
              </span>
            </div>
          )}
        </footer>
      </section>
    </div>
  );
}

function ServerRow({
  server,
  api,
  busy,
  onNotice,
}: {
  server: McpServer;
  api: McpApi;
  busy: boolean;
  onNotice: (message: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [working, setWorking] = useState<string | null>(null);
  const target = server.config.url || server.config.command || "";
  const needsAuth = server.enabled && server.status === "authorizing";

  const act = async (
    label: string,
    run: () => Promise<{ ok: boolean; error?: string }>,
  ) => {
    setWorking(label);
    onNotice(null);
    const result = await run();
    setWorking(null);
    if (!result.ok && result.error) onNotice(result.error);
  };

  return (
    <article className="rounded-card bg-ink-100 shadow-[inset_0_0_0_1px_var(--color-edge)]">
      <div className="flex items-center gap-3 px-4 py-[13px]">
        <span
          className={`inline-flex flex-none ${STATE_TONE[server.enabled ? server.status : "unknown"] ?? "text-low"}`}
          title={stateWord(server)}
        >
          <StateMark server={server} />
        </span>

        <button
          className="flex min-w-0 flex-1 flex-col items-start gap-[2px] border-none bg-transparent p-0 text-left"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          type="button"
        >
          <span className="flex max-w-full items-baseline gap-2">
            <span
              className={`overflow-hidden text-ellipsis whitespace-nowrap text-[14px] [font-weight:600] ${
                server.enabled ? "text-hi" : "text-mid"
              }`}
            >
              {server.name}
            </span>
            <span className="flex-none text-[11.5px] text-low">{stateWord(server)}</span>
          </span>
          <span className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[11px] text-low">
            {target}
          </span>
        </button>

        {needsAuth && (
          <button
            className="inline-flex h-[30px] flex-none items-center gap-[6px] rounded-[9px] border-none bg-brass px-[11px] text-[12px] [font-weight:620] text-[#241803] transition-[background-color] duration-[160ms] ease-[ease] hover:bg-[#f0c579] disabled:cursor-default disabled:opacity-60"
            onClick={() =>
              act("authorize", async () => {
                const result = await api.authorize(server.id);
                if (result.ok) onNotice(`${server.name} is connected.`);
                return result;
              })
            }
            disabled={busy || working !== null}
            type="button"
          >
            <KeyMark size={13} />
            {busy ? "Waiting…" : "Authorize"}
          </button>
        )}

        <button
          className={`inline-flex h-[30px] w-[30px] flex-none items-center justify-center rounded-[9px] border-none bg-transparent transition-[background-color,color,transform] duration-[160ms] ease-[ease] hover:bg-ink-200 hover:text-hi ${
            working === "test" ? "text-green [animation:mcp-spin_900ms_linear_infinite]" : "text-low"
          }`}
          onClick={() =>
            act("test", async () => {
              const result = await api.test(server.id);
              if (result.ok) onNotice(`${server.name} is reachable.`);
              return result;
            })
          }
          disabled={working !== null}
          aria-label={`Test ${server.name}`}
          title="Test connection"
          type="button"
        >
          <RetryMark />
        </button>

        <button
          className="inline-flex h-[30px] w-[30px] flex-none items-center justify-center rounded-[9px] border-none bg-transparent text-low transition-transform duration-[180ms] ease-[ease] hover:text-hi"
          onClick={() => setOpen((o) => !o)}
          aria-label={open ? "Hide details" : "Show details"}
          type="button"
        >
          <span className={`inline-flex transition-transform duration-[180ms] ease-[ease] ${open ? "rotate-180" : ""}`}>
            <ChevronMark />
          </span>
        </button>
      </div>

      {open && (
        <div className="px-4 pb-4 shadow-[inset_0_1px_0_var(--color-edge)]">
          {server.error && (
            <p className="mt-3 mb-0 text-[12px] leading-[1.55] text-danger">
              {server.error}
            </p>
          )}

          {server.header_names.length > 0 && (
            <p className="mt-3 mb-0 text-[12px] leading-[1.55] text-mid">
              Sends{" "}
              <span className="font-mono text-[11.5px] text-body">
                {server.header_names.join(", ")}
              </span>
              . Values are stored encrypted and never shown again.
            </p>
          )}

          {server.tools.length > 0 ? (
            <ul className="mt-3 mb-0 flex list-none flex-col gap-[7px] p-0">
              {server.tools.map((tool) => (
                <li key={tool.tool_id} className="flex items-start gap-[9px]">
                  <span className="mt-[3px] inline-flex flex-none text-green-dim">
                    <ToolMark />
                  </span>
                  <span className="min-w-0">
                    {/* The title carries the namespaced id the model actually
                        sees, so what the agent was told is inspectable without
                        putting a second monospace string on every row. */}
                    <span
                      className="block font-mono text-[12px] text-body"
                      title={tool.tool_id}
                    >
                      {tool.name}
                    </span>
                    {tool.description && (
                      <span className="block text-[11.5px] leading-[1.5] text-low">
                        {tool.description}
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 mb-0 text-[12px] leading-[1.55] text-low">
              No tools read yet. Test the connection to load them.
            </p>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              className="rounded-[9px] border-none bg-ink-200 px-[11px] py-[7px] text-[12px] [font-weight:560] text-body transition-[background-color,color] duration-[160ms] ease-[ease] hover:bg-ink-250 hover:text-hi disabled:cursor-default disabled:opacity-60"
              onClick={() =>
                act("toggle", () =>
                  api.update(server.id, { enabled: !server.enabled }),
                )
              }
              disabled={working !== null}
              type="button"
            >
              {server.enabled ? "Turn off" : "Turn on"}
            </button>

            {server.auth_mode === "oauth" && server.authorized && (
              <button
                className="rounded-[9px] border-none bg-ink-200 px-[11px] py-[7px] text-[12px] [font-weight:560] text-body transition-[background-color,color] duration-[160ms] ease-[ease] hover:bg-ink-250 hover:text-hi disabled:cursor-default disabled:opacity-60"
                onClick={() =>
                  act("disconnect", async () => {
                    const result = await api.disconnect(server.id);
                    if (result.ok) {
                      onNotice(`Signed out of ${server.name}. Its tools are paused.`);
                    }
                    return result;
                  })
                }
                disabled={working !== null}
                type="button"
              >
                Sign out
              </button>
            )}

            {confirming ? (
              <span className="inline-flex items-center gap-2">
                <button
                  className="rounded-[9px] border-none bg-danger-dim px-[11px] py-[7px] text-[12px] [font-weight:600] text-[#ffe6e1] transition-[background-color] duration-[160ms] ease-[ease] hover:bg-[#7e4740] disabled:cursor-default disabled:opacity-60"
                  onClick={() =>
                    // Announce the OUTCOME, not the intent. The delete is optimistic
                    // and rolls back on failure, so a flat "Removed X" could sit on
                    // screen right next to the restored row.
                    act("remove", async () => {
                      const result = await api.remove(server.id);
                      if (result.ok) onNotice(`Removed ${server.name}.`);
                      return result;
                    })
                  }
                  disabled={working !== null}
                  type="button"
                >
                  Remove
                </button>
                <button
                  className="rounded-[9px] border-none bg-ink-200 px-[11px] py-[7px] text-[12px] [font-weight:560] text-mid transition-[background-color,color] duration-[160ms] ease-[ease] hover:bg-ink-250 hover:text-hi"
                  onClick={() => setConfirming(false)}
                  type="button"
                >
                  Keep
                </button>
              </span>
            ) : (
              <button
                className="ml-auto inline-flex h-[30px] w-[30px] items-center justify-center rounded-[9px] border-none bg-transparent text-low transition-[background-color,color] duration-[160ms] ease-[ease] hover:bg-ink-200 hover:text-danger"
                onClick={() => setConfirming(true)}
                aria-label={`Remove ${server.name}`}
                title="Remove server"
                type="button"
              >
                <TrashMark />
              </button>
            )}
          </div>
        </div>
      )}
    </article>
  );
}

const FIELD =
  "w-full rounded-[10px] border-none bg-ink-000 px-3 py-[9px] text-[13px] text-body shadow-[inset_0_0_0_1px_var(--color-edge)] outline-none transition-[box-shadow] duration-[160ms] ease-[ease] placeholder:text-low focus:shadow-[inset_0_0_0_1px_var(--color-edge-strong)]";

const LABEL = "mb-[5px] block text-[11.5px] [font-weight:560] text-mid";

function AddServerForm({
  api,
  capabilities,
  onDone,
}: {
  api: McpApi;
  capabilities: McpCapabilities;
  onDone: (message?: string) => void;
}) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [transport, setTransport] = useState<"http" | "sse">("http");
  const [authMode, setAuthMode] = useState<McpAuthMode>("none");
  const [headerName, setHeaderName] = useState("Authorization");
  const [headerValue, setHeaderValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const nameId = useId();
  const urlId = useId();

  // Header auth needs somewhere to keep the secret. Saying so up front beats
  // accepting one and failing at the write.
  const credentialsBlocked = !capabilities.canStoreCredentials;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError("Give the server a name — it labels its tools for the agent.");
      return;
    }
    if (!url.trim()) {
      setError("Paste the server's URL.");
      return;
    }
    if (authMode === "headers" && !headerValue.trim()) {
      setError("Add the token value, or switch to no authentication.");
      return;
    }

    setSaving(true);
    const result = await api.create({
      name: name.trim(),
      url: url.trim(),
      transport,
      auth_mode: authMode,
      ...(authMode === "headers"
        ? { headers: { [headerName.trim() || "Authorization"]: headerValue.trim() } }
        : {}),
    });
    setSaving(false);

    if (!result.ok) {
      setError(result.error);
      return;
    }

    const server = result.server;
    if (server.status === "connected") {
      onDone(
        `${server.name} connected — ${server.tools.length} tool${
          server.tools.length === 1 ? "" : "s"
        } available.`,
      );
    } else if (server.status === "authorizing") {
      onDone(`${server.name} needs authorizing. Use the Authorize button on its row.`);
    } else {
      onDone(server.error ?? `${server.name} was added but could not be reached yet.`);
    }
  };

  return (
    <form className="flex flex-col gap-3" onSubmit={submit} noValidate>
      <div className="flex gap-3 [@media(max-width:520px)]:flex-col">
        <label className="min-w-0 flex-[0_0_38%]" htmlFor={nameId}>
          <span className={LABEL}>Name</span>
          <input
            id={nameId}
            className={FIELD}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="GitHub"
            maxLength={48}
            autoComplete="off"
          />
        </label>
        <label className="min-w-0 flex-1" htmlFor={urlId}>
          <span className={LABEL}>Server URL</span>
          <input
            id={urlId}
            className={FIELD}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/mcp"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
          />
        </label>
      </div>

      <fieldset className="m-0 border-none p-0">
        <legend className={LABEL}>Authentication</legend>
        {/* A segmented control, not a row of pills: one surface, the selected
            segment lifted by tone and carrying the accent lip. */}
        <div className="inline-flex gap-1 rounded-[11px] bg-ink-000 p-1 shadow-[inset_0_0_0_1px_var(--color-edge)]">
          {(
            [
              ["none", "None"],
              ["oauth", "Sign in"],
              ["headers", "Token"],
            ] as Array<[McpAuthMode, string]>
          ).map(([mode, label]) => {
            const active = authMode === mode;
            // Sign-in also needs a reachable OAuth redirect base on the backend;
            // without one the flow ends at a redirect the authorization server
            // refuses, so the option is disabled rather than offered and broken.
            const blocked =
              (credentialsBlocked && mode !== "none") ||
              (mode === "oauth" && !capabilities.canSignIn);
            return (
              <button
                key={mode}
                className={`rounded-[8px] border-none px-[13px] py-[6px] text-[12.5px] transition-[background-color,color] duration-[160ms] ease-[ease] disabled:cursor-default disabled:opacity-45 ${
                  active
                    ? "bg-ink-200 [font-weight:600] text-hi shadow-[inset_0_0_0_1px_var(--color-edge-strong)]"
                    : "bg-transparent [font-weight:520] text-mid hover:text-hi"
                }`}
                onClick={() => setAuthMode(mode)}
                disabled={blocked}
                aria-pressed={active}
                type="button"
              >
                {label}
              </button>
            );
          })}
        </div>
        <p className="mt-[7px] mb-0 text-[11.5px] leading-[1.5] text-low">
          {credentialsBlocked
            ? "This deployment cannot store credentials, so only open servers can be added."
            : authMode === "none"
              ? "For an open server. If it turns out to need a sign-in, we will offer one."
              : authMode === "oauth"
                ? "Opens the server's own sign-in in a new window. Nothing is typed here."
                : "For a server that wants a fixed API key or bearer token."}
        </p>
        {!credentialsBlocked && !capabilities.canSignIn && (
          // Describes the disabled SEGMENT, not the selected mode — so it does not
          // contradict the hint above when the user has picked None or Token.
          <p className="mt-[5px] mb-0 text-[11.5px] leading-[1.5] text-brass">
            Sign in is unavailable: the backend needs its public callback URL set
            (MCP_OAUTH_REDIRECT_BASE). Open servers and token auth work.
          </p>
        )}
      </fieldset>

      {authMode === "headers" && (
        <div className="flex gap-3 [@media(max-width:520px)]:flex-col">
          <label className="min-w-0 flex-[0_0_38%]">
            <span className={LABEL}>Header</span>
            <input
              className={`${FIELD} font-mono text-[12px]`}
              value={headerName}
              onChange={(e) => setHeaderName(e.target.value)}
              placeholder="Authorization"
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <label className="min-w-0 flex-1">
            <span className={LABEL}>Value</span>
            <input
              className={`${FIELD} font-mono text-[12px]`}
              value={headerValue}
              onChange={(e) => setHeaderValue(e.target.value)}
              placeholder="Bearer …"
              // A credential: never offer to remember or autofill it, and keep it
              // out of the accessibility value tree as plain text.
              type="password"
              autoComplete="off"
              spellCheck={false}
            />
          </label>
        </div>
      )}

      {transportNeedsChoice(url) && (
        <label className="flex items-center gap-[9px] text-[12px] text-mid">
          <input
            type="checkbox"
            className="h-[15px] w-[15px] accent-[var(--color-green)]"
            checked={transport === "sse"}
            onChange={(e) => setTransport(e.target.checked ? "sse" : "http")}
          />
          This server only speaks the older SSE transport
        </label>
      )}

      {error && (
        <p className="m-0 text-[12.5px] leading-[1.5] text-danger" role="alert">
          {error}
        </p>
      )}

      <div className="flex items-center gap-2">
        <button
          className="inline-flex h-[36px] items-center rounded-chip border-none bg-green px-4 text-[13px] [font-weight:640] text-on-accent shadow-[inset_0_1px_0_rgba(255,255,255,0.2)] transition-[background-color] duration-[180ms] ease-[ease] hover:bg-green-soft disabled:cursor-default disabled:opacity-60"
          type="submit"
          disabled={saving}
        >
          {saving ? "Checking…" : "Add server"}
        </button>
        <button
          className="rounded-[9px] border-none bg-transparent px-3 py-[8px] text-[12.5px] [font-weight:540] text-low transition-[background-color,color] duration-[160ms] ease-[ease] hover:bg-ink-150 hover:text-hi"
          onClick={() => onDone()}
          type="button"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

/** Whether to offer the SSE choice at all.
 *
 * Streamable HTTP falls back to SSE on its own, so the control is noise for a
 * normal `/mcp` URL and only earns its place when the URL itself suggests the
 * older transport. Hiding it otherwise keeps one fewer decision on screen.
 */
function transportNeedsChoice(url: string): boolean {
  return /\/sse\b/i.test(url);
}
