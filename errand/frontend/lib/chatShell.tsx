"use client";

// chatShell — the context the persistent chat shell publishes to whichever
// conversation view is mounted under it.
//
// The shell (app/(chat)/layout.tsx) owns the conversation rail and the drawer;
// the view (app/(chat)/ChatView.tsx) owns one conversation. They are separated
// exactly at the route boundary so navigating between chats remounts the view
// and leaves the rail — and its list, and its scroll position — alone. Passing
// the rail's state down through props is not possible across that boundary, so
// it comes through here instead.

import { createContext, useContext } from "react";
import type { ConversationsApi } from "./useConversations";

export interface ChatShell {
  conversations: ConversationsApi;
  token: string | null;
  userEmail: string;
  // Open the mobile drawer. The toggle lives in the view's top bar, the drawer
  // itself in the shell.
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
  // Navigate to a conversation, or to a fresh one. Real navigations, so browser
  // back works — unlike the id a first turn mints, which is pushed onto history
  // without a navigation so it cannot unmount an in-flight stream.
  openConversation: (id: string) => void;
  openNewChat: () => void;
  // Increments every time "New chat" is pressed. This is the RELIABLE reset
  // signal: after a first turn claims its id via history.pushState (not a
  // navigation), router.push("/c") is deduped as a no-op — usePathname never
  // flips back to /c, so a pathname-watch never fires and the reused ChatView
  // keeps the old chat until a refresh. A plain counter through context does not
  // depend on the router at all: it changes, ChatView sees it change, it resets.
  newChatNonce: number;
  logout: () => void;
}

const ChatShellContext = createContext<ChatShell | null>(null);

export const ChatShellProvider = ChatShellContext.Provider;

export function useChatShell(): ChatShell {
  const shell = useContext(ChatShellContext);
  if (!shell) {
    throw new Error("useChatShell must be used inside the (chat) layout.");
  }
  return shell;
}

// A conversation id in the shape the backend accepts as a primary key:
// uuid4().hex, 32 lowercase hex characters. Generated client-side so a new chat
// gets a URL and starts streaming without waiting on a round-trip to be told
// what it is called.
export function newConversationId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return uuid.replace(/-/g, "");
  // randomUUID needs a secure context. getRandomValues does not, and is enough:
  // 16 bytes of CSPRNG output rendered as hex is the same 32 characters.
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

// The conversation id in a /c/<id> pathname, or null for /c (a new chat) and for
// anything that is not a conversation route.
export function conversationIdFromPath(pathname: string | null): string | null {
  const match = /^\/c\/([0-9a-f]{32})\/?$/.exec(pathname ?? "");
  return match ? match[1] : null;
}

// Should a mounted ChatView tear its own state down and become a fresh new chat?
//
// The subtlety this exists for: a first turn claims its id with
// window.history.pushState — NOT a router navigation — so the App Router's
// rendered route stays /c while the URL reads /c/<id>. Clicking "New chat" then
// calls router.push("/c"), which the router treats as a no-op because /c is
// already the rendered route: the ChatView instance is reused, its activeId
// never clears, and the old conversation (and its cart) stays on screen until a
// hard refresh. usePathname DOES see the pushState'd and pushed URLs both, so the
// view watches the route and resets itself the moment the route says "new chat"
// (routeId === null) while it is still bound to an id.
//
// Pure so it can be pinned by a test without a router. Returns true ONLY when the
// route is the new-chat route but the view still holds a conversation — never on
// a normal /c→/c/<id> first turn (routeId is the id, not null) and never on a
// fresh mount at /c (boundId is already null).
export function shouldResetToNewChat(
  routeId: string | null,
  boundId: string | null,
): boolean {
  return routeId === null && boundId !== null;
}

// The PRIMARY new-chat reset decision: has the shell's newChatNonce moved since
// this ChatView last acted on it? Pure so it can be pinned without a router or a
// rendered component. `seen` is the last value the view handled; on the first
// render seen === current, so this is false and a freshly-seeded conversation is
// never wiped. Every press of "New chat" bumps the nonce, so it returns true
// exactly once per press — independent of the router deduping the /c push.
export function nonceRequestsReset(current: number, seen: number): boolean {
  return current !== seen;
}
