// Pins the routing contract that made "New chat" require a refresh and made a
// typed turn after voice open a second conversation.
//
// A first turn claims its id with history.pushState, not a navigation, so the
// App Router keeps the ChatView mounted while the URL reads /c/<id>. The reset
// that turns a reused ChatView back into a blank new chat is gated on this pure
// decision — if it regresses, "New chat" silently stops working again.

import { describe, expect, it } from "vitest";
import { conversationIdFromPath, shouldResetToNewChat } from "./chatShell";

const ID = "a".repeat(32);

describe("conversationIdFromPath", () => {
  it("reads the id from /c/<id> (with and without a trailing slash)", () => {
    expect(conversationIdFromPath(`/c/${ID}`)).toBe(ID);
    expect(conversationIdFromPath(`/c/${ID}/`)).toBe(ID);
  });

  it("is null for the new-chat route and non-conversation routes", () => {
    expect(conversationIdFromPath("/c")).toBeNull();
    expect(conversationIdFromPath("/c/")).toBeNull();
    expect(conversationIdFromPath("/login")).toBeNull();
    expect(conversationIdFromPath(null)).toBeNull();
  });

  it("rejects a malformed id rather than binding to a bad conversation", () => {
    expect(conversationIdFromPath("/c/not-hex")).toBeNull();
    expect(conversationIdFromPath(`/c/${"a".repeat(31)}`)).toBeNull();
    expect(conversationIdFromPath(`/c/${"a".repeat(33)}`)).toBeNull();
  });
});

describe("shouldResetToNewChat", () => {
  it("resets when the route says new-chat but the view still holds an id", () => {
    // The exact bug: pushState left the URL at /c/<id>, then New chat pushed /c
    // (routeId → null) but the instance was reused (boundId still the old id).
    expect(shouldResetToNewChat(null, ID)).toBe(true);
  });

  it("does NOT reset on a first turn (route becomes the id, not null)", () => {
    // /c → /c/<id> via pushState: routeId is the new id, so no reset — a reset
    // here would wipe the very stream that just claimed the id.
    expect(shouldResetToNewChat(ID, ID)).toBe(false);
  });

  it("does NOT reset a fresh new chat (nothing bound yet)", () => {
    expect(shouldResetToNewChat(null, null)).toBe(false);
  });

  it("does NOT reset while a conversation is open and matches the route", () => {
    expect(shouldResetToNewChat(ID, ID)).toBe(false);
    const other = "b".repeat(32);
    // Switching to another chat is a real navigation that remounts a keyed view;
    // this decision is only about the reused /c instance, so a mismatch that is
    // not the new-chat route must not trigger the blank-out.
    expect(shouldResetToNewChat(other, ID)).toBe(false);
  });
});
