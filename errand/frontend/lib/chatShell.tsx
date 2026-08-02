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
