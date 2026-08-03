"use client";

/* The persistent chat shell.
 *
 * This is a route-group layout, so it is a SIBLING of {children} rather than
 * their parent component — React keeps it mounted across every navigation
 * between /c and /c/<id>. That is the whole point: the conversation rail fetches
 * its list once per session instead of once per chat, and keeps its scroll
 * position and its open delete-confirm while the thread underneath it changes.
 *
 * It also holds the route guard, so no chat route can render before we know who
 * is signed in. */

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth";
import { useConversations } from "@/lib/useConversations";
import { useMcpServers } from "@/lib/useMcpServers";
import { ChatShellProvider, conversationIdFromPath } from "@/lib/chatShell";
import { api } from "@/lib/config";
import Sidebar from "@/components/Sidebar";
import McpPanel, { type McpCapabilities } from "@/components/mcp/McpPanel";
import { ErrandMark } from "@/components/Marks";

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, token, loading: authLoading, logout } = useAuth();

  const [drawerOpen, setDrawerOpen] = useState(false);
  // Bumped by openNewChat. The reused-ChatView reset keys off this, not the
  // pathname, because router.push("/c") after a pushState is deduped and the
  // pathname never changes — so a pathname-only signal silently never fires.
  const [newChatNonce, setNewChatNonce] = useState(0);
  const conversations = useConversations(token);

  // MCP tool servers. Lives here rather than in the panel so the list survives
  // closing the sheet, and so the rail can show the count without opening it.
  const mcp = useMcpServers(token);
  const [toolsOpen, setToolsOpen] = useState(false);
  // What this deployment will actually accept. Read from /api/config so the UI
  // never offers a control the backend refuses (a local-command server, or
  // credential auth where no encryption key is configured). Null until known,
  // which is what keeps the entry point hidden rather than flashing it.
  const [mcpCaps, setMcpCaps] = useState<McpCapabilities | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch(api("/api/config"));
        if (!res.ok || !alive) return;
        const body = (await res.json()) as {
          mcp?: {
            enabled?: boolean;
            allowStdio?: boolean;
            maxServers?: number;
            canStoreCredentials?: boolean;
            canSignIn?: boolean;
          };
        };
        if (!alive || !body.mcp?.enabled) return;
        setMcpCaps({
          allowStdio: Boolean(body.mcp.allowStdio),
          maxServers: body.mcp.maxServers ?? 12,
          canStoreCredentials: Boolean(body.mcp.canStoreCredentials),
          canSignIn: Boolean(body.mcp.canSignIn),
        });
      } catch {
        /* readiness unknown — the entry point stays hidden */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Send anyone without a session to the door. Guarding here rather than in each
  // page means no chat route can render, or fire a request, before we know.
  useEffect(() => {
    if (!authLoading && !user) router.replace("/login");
  }, [authLoading, user, router]);

  // The rail highlights whatever the URL says. window.history.pushState — which
  // is how a first turn claims its id without unmounting its own stream — is
  // wired into the App Router, so usePathname sees that too.
  const activeId = conversationIdFromPath(pathname);

  const openConversation = useCallback(
    (id: string) => {
      setDrawerOpen(false);
      router.push(`/c/${id}`);
    },
    [router],
  );

  const openNewChat = useCallback(() => {
    setDrawerOpen(false);
    // Bump the nonce FIRST — this is the signal ChatView actually resets on. The
    // router.push keeps the URL/history honest (and does remount when coming from
    // a real /c/<id> route), but it is deduped and does nothing when we are on
    // the reused /c instance, which is exactly the case that was broken.
    setNewChatNonce((n) => n + 1);
    router.push("/c");
  }, [router]);

  const signOut = useCallback(() => {
    logout();
    router.replace("/login");
  }, [logout, router]);

  const deleteConversation = useCallback(
    (id: string) => {
      void conversations.remove(id);
      // Deleting the chat you are reading leaves you on a URL that no longer
      // resolves, so step off it. Everything else keeps its place.
      if (id === activeId) router.replace("/c");
    },
    [conversations, activeId, router],
  );

  const shell = useMemo(
    () => ({
      conversations,
      token,
      userEmail: user?.email ?? "",
      drawerOpen,
      setDrawerOpen,
      openConversation,
      openNewChat,
      newChatNonce,
      logout: signOut,
    }),
    [conversations, token, user?.email, drawerOpen, openConversation, openNewChat, newChatNonce, signOut],
  );

  if (authLoading || !user) {
    // Not an entrance animation gating content — there is genuinely nothing to
    // show yet. The redirect fires from the auth-aware route, not from here, so
    // this never sits on screen indefinitely.
    return (
      <div
        className="relative z-[1] h-dvh flex flex-col items-center justify-center gap-4"
        role="status"
        aria-live="polite"
      >
        <span className="inline-flex text-green">
          <ErrandMark size={40} />
        </span>
        <span className="text-[14px] tracking-[0.01em] text-mid">
          {authLoading ? "Restoring your session…" : "Redirecting to sign in…"}
        </span>
      </div>
    );
  }

  return (
    <ChatShellProvider value={shell}>
      <div className="relative z-[1] grid h-dvh grid-cols-[264px_1fr] overflow-hidden [@media(max-width:860px)]:grid-cols-[1fr]">
        {drawerOpen && (
          <button
            className="hidden border-none bg-[rgba(4,8,6,0.6)] [@media(max-width:860px)]:block [@media(max-width:860px)]:fixed [@media(max-width:860px)]:inset-0 [@media(max-width:860px)]:z-[35]"
            aria-label="Close conversation list"
            onClick={() => setDrawerOpen(false)}
          />
        )}

        <div
          className={`min-h-0 h-full [@media(max-width:860px)]:fixed [@media(max-width:860px)]:top-0 [@media(max-width:860px)]:left-0 [@media(max-width:860px)]:bottom-0 [@media(max-width:860px)]:z-40 [@media(max-width:860px)]:w-[280px] [@media(max-width:860px)]:shadow-[18px_0_46px_-26px_rgba(4,8,6,0.9)] [@media(max-width:860px)]:transition-transform [@media(max-width:860px)]:duration-[240ms] [@media(max-width:860px)]:ease-[cubic-bezier(0.22,0.8,0.28,1)] ${
            drawerOpen
              ? "[@media(max-width:860px)]:[transform:translateX(0)]"
              : "[@media(max-width:860px)]:[transform:translateX(-102%)]"
          }`}
        >
          <Sidebar
            conversations={conversations.conversations}
            activeId={activeId}
            userEmail={user.email}
            loading={conversations.loading}
            onSelect={openConversation}
            onNew={openNewChat}
            onDelete={deleteConversation}
            onLogout={signOut}
            onCloseMobile={() => setDrawerOpen(false)}
            onOpenTools={mcpCaps ? () => setToolsOpen(true) : undefined}
            toolServerCount={mcp.servers.filter((s) => s.enabled).length}
          />
        </div>

        <div className="relative flex min-w-0 flex-col overflow-hidden">{children}</div>
      </div>

      {toolsOpen && mcpCaps && (
        <McpPanel
          api={mcp}
          capabilities={mcpCaps}
          onClose={() => setToolsOpen(false)}
        />
      )}
    </ChatShellProvider>
  );
}
