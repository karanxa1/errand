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
import { ChatShellProvider, conversationIdFromPath } from "@/lib/chatShell";
import Sidebar from "@/components/Sidebar";
import { ErrandMark } from "@/components/Marks";

import css from "./chat.module.css";

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, token, loading: authLoading, logout } = useAuth();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const conversations = useConversations(token);

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
      logout: signOut,
    }),
    [conversations, token, user?.email, drawerOpen, openConversation, openNewChat, signOut],
  );

  if (authLoading || !user) {
    // Not an entrance animation gating content — there is genuinely nothing to
    // show yet. The redirect fires from the auth-aware route, not from here, so
    // this never sits on screen indefinitely.
    return (
      <div className={css.boot} role="status" aria-live="polite">
        <span className={css.bootMark}>
          <ErrandMark size={40} />
        </span>
        <span className={css.bootText}>
          {authLoading ? "Restoring your session…" : "Redirecting to sign in…"}
        </span>
      </div>
    );
  }

  return (
    <ChatShellProvider value={shell}>
      <div className={css.shell}>
        {drawerOpen && (
          <button
            className={css.scrim}
            aria-label="Close conversation list"
            onClick={() => setDrawerOpen(false)}
          />
        )}

        <div className={`${css.rail} ${drawerOpen ? css.railOpen : ""}`}>
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
          />
        </div>

        <div className={css.main}>{children}</div>
      </div>
    </ChatShellProvider>
  );
}
