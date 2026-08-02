# SSR Session Seed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a best-effort first-party httpOnly JWT mirror so authenticated hard loads of `/c/[id]` can render the latest conversation window in server HTML without changing direct SSE, voice, or mutation traffic.

**Architecture:** The browser continues using its localStorage Bearer token for all existing backend calls. A same-origin Next route mirrors that token into a first-party cookie; the conversation server component reads it and performs one server-to-server authenticated GET. A nullable seed is passed to `ChatView` and `useChat`, while the current client fetch remains canonical fallback.

**Tech Stack:** Next.js 15 App Router, React 19, TypeScript 5.7, Vitest 4, Testing Library, OpenNext Cloudflare Workers.

## Global Constraints

- Do not proxy SSE, voice WebSocket, voice-ticket, approval, or mutation requests through Next.
- Do not remove the JWT from localStorage or claim that this removes XSS exposure.
- Cookie synchronization is best-effort and must never block login, registration, hydration, or logout.
- The cookie is `httpOnly`, `sameSite=lax`, path `/`, seven-day max age, and `secure` outside development.
- Missing, stale, rejected, or unreachable server seeds return `null` without exposing backend errors.
- A valid seed remains visible while client authentication hydrates.
- Tailwind v4 only; no CSS Modules and no PostCSS configuration change.

---

### Task 1: First-Party Session Mirror

**Files:**
- Create: `errand/frontend/app/api/session/route.ts`
- Create: `errand/frontend/lib/sessionMirror.ts`
- Create: `errand/frontend/lib/sessionMirror.test.ts`

**Interfaces:**
- Produces: `mirrorSessionToken(token: string | null): Promise<boolean>` for `AuthProvider`.
- Produces: `POST /api/session` body `{ token: string }` and `DELETE /api/session`.

- [ ] **Step 1: Write the failing browser helper tests**

```ts
// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mirrorSessionToken } from "./sessionMirror";

describe("mirrorSessionToken", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("POSTs a token to the same-origin session route", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true }) as Response);
    vi.stubGlobal("fetch", fetchMock);
    await expect(mirrorSessionToken("jwt-value")).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledWith("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: "jwt-value" }),
    });
  });

  it("DELETEs the mirror on logout", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true }) as Response);
    vi.stubGlobal("fetch", fetchMock);
    await expect(mirrorSessionToken(null)).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledWith("/api/session", { method: "DELETE" });
  });

  it("reports failure instead of throwing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    await expect(mirrorSessionToken("jwt-value")).resolves.toBe(false);
  });
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx vitest run lib/sessionMirror.test.ts`

Expected: FAIL because `./sessionMirror` does not exist.

- [ ] **Step 3: Implement the best-effort browser helper**

```ts
export async function mirrorSessionToken(token: string | null): Promise<boolean> {
  try {
    const response = await fetch(
      "/api/session",
      token
        ? {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token }),
          }
        : { method: "DELETE" },
    );
    return response.ok;
  } catch {
    return false;
  }
}
```

- [ ] **Step 4: Implement the session route**

```ts
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

const COOKIE = "errand_session";
const MAX_AGE = 60 * 60 * 24 * 7;

export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as { token?: unknown } | null;
  if (!body || typeof body.token !== "string" || body.token.length < 20 || body.token.length > 4096) {
    return NextResponse.json({ detail: "Invalid session token" }, { status: 400 });
  }
  const store = await cookies();
  store.set(COOKIE, body.token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV !== "development",
    path: "/",
    maxAge: MAX_AGE,
  });
  return new NextResponse(null, { status: 204 });
}

export async function DELETE() {
  const store = await cookies();
  store.set(COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV !== "development",
    path: "/",
    maxAge: 0,
  });
  return new NextResponse(null, { status: 204 });
}
```

- [ ] **Step 5: Run the focused test and verify GREEN**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx vitest run lib/sessionMirror.test.ts`

Expected: `3 passed`.

- [ ] **Step 6: Commit the session mirror**

```bash
git add errand/frontend/app/api/session/route.ts errand/frontend/lib/sessionMirror.ts errand/frontend/lib/sessionMirror.test.ts
git commit -m "feat(frontend): add a first-party session mirror"
```

### Task 2: Synchronize Authentication Without Blocking It

**Files:**
- Modify: `errand/frontend/lib/auth.tsx:1-192`

**Interfaces:**
- Consumes: `mirrorSessionToken(token: string | null): Promise<boolean>`.
- Preserves: `AuthValue` shape and all localStorage/Bearer behavior.

- [ ] **Step 1: Add the helper import**

```ts
import { mirrorSessionToken } from "./sessionMirror";
```

- [ ] **Step 2: Mirror stored sessions after localStorage hydration**

Immediately after `setToken(stored)`, start best-effort synchronization without awaiting it:

```ts
void mirrorSessionToken(stored);
```

- [ ] **Step 3: Mirror successful login and registration**

After `writeStoredToken(data.token)` in both functions:

```ts
void mirrorSessionToken(data.token);
```

- [ ] **Step 4: Clear the mirror during logout**

```ts
const logout = useCallback(() => {
  writeStoredToken(null);
  void mirrorSessionToken(null);
  setToken(null);
  setUser(null);
}, []);
```

- [ ] **Step 5: Run auth-adjacent tests and typecheck**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx vitest run lib/sessionMirror.test.ts lib/useVoiceAgent.test.tsx`

Expected: session mirror tests and all six existing voice tests pass.

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx tsc --noEmit`

Expected: exit 0.

- [ ] **Step 6: Commit auth synchronization**

```bash
git add errand/frontend/lib/auth.tsx
git commit -m "feat(frontend): synchronize the server session seed"
```

### Task 3: Server Conversation Seed

**Files:**
- Create: `errand/frontend/lib/serverConversation.ts`
- Create: `errand/frontend/lib/serverConversation.test.ts`

**Interfaces:**
- Produces: `fetchConversationSeed(id: string, token: string | undefined): Promise<ConversationDetail | null>`.

- [ ] **Step 1: Write the failing seed-helper tests**

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchConversationSeed } from "./serverConversation";

describe("fetchConversationSeed", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns null without a token", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchConversationSeed("a".repeat(32), undefined)).resolves.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns an authenticated conversation", async () => {
    const detail = { id: "a".repeat(32), title: "Office", profile: "business", model: "sol", created_at: "x", updated_at: "x", messages: [] };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => detail }) as Response));
    await expect(fetchConversationSeed(detail.id, "jwt-value")).resolves.toEqual(detail);
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining(`/api/conversations/${detail.id}`), {
      headers: { Authorization: "Bearer jwt-value" },
      cache: "no-store",
    });
  });

  it("returns null for upstream rejection", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false }) as Response));
    await expect(fetchConversationSeed("a".repeat(32), "jwt-value")).resolves.toBeNull();
  });
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx vitest run lib/serverConversation.test.ts`

Expected: FAIL because `./serverConversation` does not exist.

- [ ] **Step 3: Implement the nullable server helper**

```ts
import { api } from "./config";
import type { ConversationDetail } from "./useChat";

export async function fetchConversationSeed(id: string, token: string | undefined) {
  if (!token || !/^[0-9a-f]{32}$/.test(id)) return null;
  try {
    const response = await fetch(api(`/api/conversations/${id}`), {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    return response.ok ? (await response.json()) as ConversationDetail : null;
  } catch {
    return null;
  }
}
```

- [ ] **Step 4: Run seed tests and typecheck**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx vitest run lib/serverConversation.test.ts`

Expected: `3 passed`.

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx tsc --noEmit`

Expected: exit 0.

- [ ] **Step 5: Commit the server seed helper**

```bash
git add errand/frontend/lib/serverConversation.ts errand/frontend/lib/serverConversation.test.ts
git commit -m "feat(frontend): add a nullable conversation seed fetch"
```

### Task 4: Seed Chat State Without a Hydration Flash

**Files:**
- Modify: `errand/frontend/app/(chat)/c/[id]/page.tsx:1-17`
- Modify: `errand/frontend/app/(chat)/ChatView.tsx:59-104`
- Modify: `errand/frontend/app/(chat)/c/page.tsx:4-8`
- Modify: `errand/frontend/lib/useChat.ts:74-255`
- Create: `errand/frontend/lib/useChat.seed.test.tsx`

**Interfaces:**
- Adds: `UseChatArgs.initialDetail?: ConversationDetail | null`.
- Adds: `ChatViewProps.initialDetail?: ConversationDetail | null`.

- [ ] **Step 1: Write the failing hook seed test**

```tsx
// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useChat, type ConversationDetail } from "./useChat";

it("renders a matching server seed while the browser token hydrates", () => {
  const detail: ConversationDetail = {
    id: "a".repeat(32), title: "Office", profile: "business", model: "sol",
    created_at: "x", updated_at: "x",
    messages: [{ id: "m1", role: "assistant", content: "Seeded", created_at: "x" }],
  };
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  const { result } = renderHook(() => useChat({ conversationId: detail.id, token: null, initialDetail: detail }));
  expect(result.current.messages).toEqual(detail.messages);
  expect(fetchMock).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run the seed test and verify RED**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx vitest run lib/useChat.seed.test.tsx`

Expected: FAIL because `initialDetail` is not accepted and messages initialize empty.

- [ ] **Step 3: Add seed-aware state to `useChat`**

Add `initialDetail?: ConversationDetail | null` to `UseChatArgs`, destructure it, and initialize:

```ts
const matchingSeed = initialDetail?.id === conversationId ? initialDetail : null;
const [messages, setMessages] = useState<ChatMessage[]>(matchingSeed?.messages ?? []);
```

In the `!conversationId || !token` branch, retain a matching seed:

```ts
if (!conversationId || !token) {
  setMessages(initialDetail?.id === conversationId ? initialDetail.messages : []);
  setLoading(false);
  return;
}
```

Include `initialDetail` in the load-effect dependency list. When a matching seed exists, call `onLoadedRef.current?.(initialDetail)` before the browser refresh so model/profile are correct on first paint.

- [ ] **Step 4: Thread the seed through `ChatView`**

```ts
interface ChatViewProps {
  initialId: string | null;
  initialDetail?: ConversationDetail | null;
}

export default function ChatView({ initialId, initialDetail = null }: ChatViewProps) {
  const [model, setModel] = useState(initialDetail?.model ?? "sol");
  const [profile, setProfile] = useState<ProfileKind>(initialDetail?.profile ?? "business");
  // ...
  const chat = useChat({ conversationId: activeId, token, initialDetail, onTitle, onTurnComplete, onLoaded });
}
```

Make the new-chat call explicit:

```tsx
return <ChatView initialId={null} initialDetail={null} />;
```

- [ ] **Step 5: Convert the conversation page to an async server component**

```tsx
import { cookies } from "next/headers";
import ChatView from "../../ChatView";
import { fetchConversationSeed } from "@/lib/serverConversation";

export default async function ConversationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const token = (await cookies()).get("errand_session")?.value;
  const initialDetail = await fetchConversationSeed(id, token);
  return <ChatView key={id} initialId={id} initialDetail={initialDetail} />;
}
```

- [ ] **Step 6: Run focused and full frontend tests**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx vitest run lib/useChat.seed.test.tsx`

Expected: seed test passes.

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx vitest run`

Expected: all existing and new tests pass.

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx tsc --noEmit`

Expected: exit 0.

- [ ] **Step 7: Commit seed integration**

```bash
git add "errand/frontend/app/(chat)/c/[id]/page.tsx" "errand/frontend/app/(chat)/ChatView.tsx" "errand/frontend/app/(chat)/c/page.tsx" errand/frontend/lib/useChat.ts errand/frontend/lib/useChat.seed.test.tsx
git commit -m "feat(frontend): seed chat history from server HTML"
```

### Task 5: SSR Verification

**Files:**
- Modify after verification: `CONTEXT.md`

- [ ] **Step 1: Run the complete local gate once**

Run: `PATH=/Users/macbook/.bun/bin:$PATH bunx tsc --noEmit && bunx vitest run && bunx next build`

Expected: typecheck exit 0, all tests pass, production build succeeds.

- [ ] **Step 2: Verify no streaming path changed**

Run: `git diff 48ddb33 -- errand/frontend/lib/useChat.ts errand/frontend/lib/useVoiceAgent.ts errand/frontend/lib/useConversations.ts`

Expected: `useChat.ts` changes only seed initialization/effect behavior; no URL, method, authorization header, SSE reader, approval route, or voice code change.

- [ ] **Step 3: Document the exact boundary**

Add to `CONTEXT.md` under frontend auth:

```md
- `/api/session` mirrors the browser JWT into a first-party httpOnly Worker cookie solely so `/c/[id]` can SSR its latest message window. Browser API, SSE, approval, and voice traffic still goes directly to the backend with the localStorage Bearer token; this mirror does not remove that XSS exposure.
```

- [ ] **Step 4: Commit documentation**

```bash
git add CONTEXT.md
git commit -m "docs: record the SSR session boundary"
```
