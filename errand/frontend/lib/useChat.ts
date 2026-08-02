// useChat — owns ONE persisted conversation: its message history, the live
// streaming turn, and the human-in-the-loop approval gate.
//
// Loading history: GET /api/conversations/{id} returns messages with role
// user | assistant | tool. A tool message carries an `events` array of frame
// objects (each with a `type`); replaying those through the SHARED reducer
// (lib/errandReducer.applyFrame) rebuilds the exact RunState the chat thread
// renders as animated tool cards — the same render path as a live run.
//
// Sending a turn: POST /api/conversations/{id}/chat streams SSE. EventSource
// can't POST, so we open with fetch() and hand-parse `event:` / `data:` frames
// (mirroring lib/stream.ts). `token` frames stream into the live assistant
// bubble; tool / errand / websearch / approval frames fold through applyFrame
// into a live RunState so tool cards animate inline; `title` renames the sidebar
// item; `done` ends the stream.
//
// When the turn ends it is COMMITTED LOCALLY into `messages` — the exact user
// text, the exact RunState the operator watched build, and the assistant text the
// server confirmed via `assistant.saved`. It is deliberately not re-fetched: the
// stream already delivered every part of the turn, so a GET of the whole
// conversation would spend a round-trip to be told what we just watched happen,
// and would briefly re-render the thread from scratch on the way. A genuine
// reload of the page still reads canonical state from the server.
//
// The conversation row itself is created lazily server-side on the first turn, so
// `send` carries the profile/model to seed it with. That is why a brand-new chat
// needs no blocking POST before the first token.
//
// Every request carries Authorization: Bearer <token> from useAuth.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./config";
import {
  applyFrame,
  initialRunState,
  type RunState,
} from "./errandReducer";
import type { ApprovalResult, ProfileKind, RawFrame } from "./types";

export type ChatRole = "user" | "assistant" | "tool";

// One persisted message. For role="tool", `events` is the ordered frame list to
// replay through applyFrame; for user/assistant it's plain `content`.
export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  events?: (Record<string, unknown> & { type: string })[];
  // Set only on a turn committed locally from the live stream (role="tool"): the
  // finished RunState exactly as it was rendered, so committing the turn does not
  // have to round-trip the frames back through the reducer to say the same thing.
  // Server-loaded tool messages carry `events` instead and are replayed.
  runState?: RunState;
  created_at: string;
}

// Conversation list item (GET /api/conversations).
export interface Conversation {
  id: string;
  title: string;
  profile: ProfileKind;
  model: string;
  created_at: string;
  updated_at: string;
}

// Full conversation (GET /api/conversations/{id}) — metadata + messages.
export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}

interface UseChatArgs {
  conversationId: string | null;
  token: string | null;
  // Fired when the backend auto-titles a conversation on its first turn.
  onTitle?: (conversationId: string, title: string) => void;
  // Fired once a turn has finished and been committed, so the rail can re-sort.
  onTurnComplete?: (conversationId: string) => void;
  // Fired after a conversation's full detail loads (to sync model/profile).
  onLoaded?: (detail: ConversationDetail) => void;
  // A server-rendered seed for first paint, shown before the browser token
  // hydrates. Best-effort only; the canonical Bearer fetch still runs.
  initialDetail?: ConversationDetail | null;
}

// What a turn needs beyond its text. `conversationId` overrides the hook's
// current id (the caller has just minted one for a brand-new chat and the state
// update has not landed yet); profile/model seed the conversation row if this
// turn is the one that creates it.
export interface SendOptions {
  conversationId?: string;
  profile?: ProfileKind;
  model?: string;
}

export interface ChatApi {
  messages: ChatMessage[];
  loading: boolean;
  streaming: boolean;
  error: string | null;
  // The just-sent user turn, shown immediately (before the turn is committed).
  liveUser: string | null;
  // The assistant text streaming in token-by-token.
  liveAssistant: string;
  // The live errand/tool RunState for the current turn (drives Thread cards).
  liveRun: RunState;
  send: (content: string, opts?: SendOptions) => Promise<void>;
  resolveApproval: (verdict: ApprovalResult) => Promise<void>;
}

// Rebuild a RunState from a saved tool message's frame list. Each event is a
// plain object with a `type`; applyFrame wants { event, data } — the whole event
// doubles as the data payload (raw events read top-level fields; wrapped audit
// events read .step/.detail/.data), exactly as the live wire does.
export function runStateFromEvents(
  events: (Record<string, unknown> & { type: string })[] | undefined,
): RunState {
  let state = initialRunState;
  for (const ev of events ?? []) {
    state = applyFrame(state, { event: ev.type, data: ev });
  }
  return state;
}

// ── SSE frame parsing (mirrors lib/stream.ts) ───────────────────────────────
function indexOfFrameBoundary(buf: string): number {
  const a = buf.indexOf("\n\n");
  const b = buf.indexOf("\r\n\r\n");
  if (a === -1) return b;
  if (b === -1) return a;
  return Math.min(a, b);
}

function parseFrame(raw: string): RawFrame | null {
  const lines = raw.split(/\r?\n/);
  let event = "message";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

// Fold a frame into a RunState, marking the connection open on first contact so
// the thread's "working…" shimmer shows while an errand step is in flight.
function foldLive(state: RunState, frame: RawFrame): RunState {
  const opened =
    state.connection === "open" ? state : { ...state, connection: "open" as const };
  return applyFrame(opened, frame);
}

export function useChat({
  conversationId,
  token,
  onTitle,
  onTurnComplete,
  onLoaded,
  initialDetail,
}: UseChatArgs): ChatApi {
  const matchingSeed = initialDetail?.id === conversationId ? initialDetail : null;
  const [messages, setMessages] = useState<ChatMessage[]>(matchingSeed?.messages ?? []);
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [liveUser, setLiveUser] = useState<string | null>(null);
  const [liveAssistant, setLiveAssistant] = useState("");
  const [liveRun, setLiveRun] = useState<RunState>(initialRunState);

  // Callbacks change identity across renders; hold them in refs so effects and
  // stream handlers always call the latest without re-subscribing.
  const onTitleRef = useRef(onTitle);
  const onTurnCompleteRef = useRef(onTurnComplete);
  const onLoadedRef = useRef(onLoaded);
  onTitleRef.current = onTitle;
  onTurnCompleteRef.current = onTurnComplete;
  onLoadedRef.current = onLoaded;

  // Current conversation + live run, mirrored into refs for stream handlers.
  const convIdRef = useRef<string | null>(conversationId);
  convIdRef.current = conversationId;
  const tokenRef = useRef<string | null>(token);
  tokenRef.current = token;
  const liveRunRef = useRef<RunState>(liveRun);
  liveRunRef.current = liveRun;
  const runIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // The conversation id the in-flight stream belongs to (may be an override
  // passed to send() for a just-created conversation).
  const streamIdRef = useRef<string | null>(null);

  const clearLive = useCallback(() => {
    setLiveUser(null);
    setLiveAssistant("");
    setLiveRun(initialRunState);
    runIdRef.current = null;
  }, []);

  // Load history whenever the active conversation changes. Aborts any in-flight
  // stream from the previous conversation.
  useEffect(() => {
    // A freshly-created conversation whose FIRST turn is already streaming: the
    // send() started before this effect saw the new id. Don't abort that stream
    // or wipe the live turn — just show empty history beneath it (a new
    // conversation has none). The stream's own reload() will fill it in.
    if (abortRef.current && streamIdRef.current === conversationId) {
      setMessages([]);
      setLoading(false);
      return;
    }

    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setError(null);
    clearLive();

    if (!conversationId || !token) {
      setMessages(
        initialDetail?.id === conversationId ? (initialDetail.messages ?? []) : [],
      );
      setLoading(false);
      return;
    }

    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const res = await fetch(api(`/api/conversations/${conversationId}`), {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!alive) return;
        if (!res.ok) {
          setMessages([]);
          return;
        }
        const detail = (await res.json()) as ConversationDetail;
        if (!alive) return;
        setMessages(detail.messages ?? []);
        onLoadedRef.current?.(detail);
      } catch {
        if (alive) setMessages([]);
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, [conversationId, token, clearLive, initialDetail]);

  const send = useCallback(
    async (content: string, opts?: SendOptions) => {
      const id = opts?.conversationId ?? convIdRef.current;
      const tk = tokenRef.current;
      const text = content.trim();
      if (!id || !tk || !text) return;

      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      streamIdRef.current = id;
      runIdRef.current = null;

      setError(null);
      setLiveUser(text);
      setLiveAssistant("");
      setLiveRun({ ...initialRunState, connection: "open" });
      setStreaming(true);

      // The turn accumulated synchronously, alongside the React state. Committing
      // the turn at the end needs the FINAL values, and reading them back out of
      // state (or a ref mirroring state) at that moment is a race — the last
      // setState of the stream may not have rendered yet. These are the truth;
      // the setState calls are how it gets on screen.
      let runAcc: RunState = { ...initialRunState, connection: "open" };
      let textAcc = "";
      let savedMessageId: string | null = null;

      // Handle one decoded frame. `id` is captured so `title` targets the right
      // conversation even for a freshly-created one.
      const handleFrame = (frame: RawFrame) => {
        const { event } = frame;
        const data = (frame.data ?? {}) as Record<string, unknown>;
        switch (event) {
          case "token": {
            const t = (data.text as string) ?? "";
            if (t) {
              textAcc += t;
              setLiveAssistant(textAcc);
            }
            return;
          }
          case "title": {
            const title = (data.title as string) ?? "";
            if (title) onTitleRef.current?.(id, title);
            return;
          }
          case "assistant.saved": {
            // The persisted final text and its real row id. Adopting both means
            // the committed message is identical to what a later page load will
            // read back from the server, id included.
            const finalText = data.content as string | undefined;
            if (typeof finalText === "string") {
              textAcc = finalText;
              setLiveAssistant(finalText);
            }
            if (typeof data.message_id === "string") {
              savedMessageId = data.message_id;
            }
            return;
          }
          case "done":
            return;
          case "error": {
            setError((data.message as string) ?? "The chat stream errored.");
            return;
          }
          case "approval.request": {
            if (typeof data.run_id === "string") runIdRef.current = data.run_id;
            runAcc = foldLive(runAcc, frame);
            setLiveRun(runAcc);
            return;
          }
          default:
            // tool.call, tool.result, websearch.result, and every errand audit
            // event (run.started … run.done) fold into the live RunState.
            runAcc = foldLive(runAcc, frame);
            setLiveRun(runAcc);
        }
      };

      try {
        const res = await fetch(api(`/api/conversations/${id}/chat`), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${tk}`,
          },
          body: JSON.stringify({
            content: text,
            // Read server-side only when this turn creates the conversation.
            ...(opts?.profile ? { profile: opts.profile } : {}),
            ...(opts?.model ? { model: opts.model } : {}),
          }),
          signal: ctrl.signal,
        });
        if (!res.ok || !res.body) {
          throw new Error(`The backend responded ${res.status}.`);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let sep: number;
          while ((sep = indexOfFrameBoundary(buffer)) !== -1) {
            const rawFrame = buffer.slice(0, sep);
            buffer = buffer.slice(sep).replace(/^(\r?\n){2}/, "");
            const parsed = parseFrame(rawFrame);
            if (parsed) handleFrame(parsed);
          }
        }
      } catch (err) {
        if (!ctrl.signal.aborted) {
          setError((err as Error).message || "The chat stream failed.");
        }
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null;
        if (!ctrl.signal.aborted) {
          setStreaming(false);
          // Commit the turn into history. This runs even if the stream errored
          // part-way: the operator watched those tokens and those tool cards
          // arrive, and dropping them because the tail failed would delete
          // content off the screen. Whatever did arrive stays.
          if (convIdRef.current === id || streamIdRef.current === id) {
            const now = new Date().toISOString();
            const seq = `${id}-${Date.now()}`;
            const committed: ChatMessage[] = [
              { id: `u-${seq}`, role: "user", content: text, created_at: now },
            ];
            if (runAcc.audit.length > 0) {
              committed.push({
                id: `t-${seq}`,
                role: "tool",
                content: "",
                runState: runAcc,
                created_at: now,
              });
            }
            if (textAcc.trim()) {
              committed.push({
                id: savedMessageId ?? `a-${seq}`,
                role: "assistant",
                content: textAcc,
                created_at: now,
              });
            }
            setMessages((prev) => [...prev, ...committed]);
            clearLive();
          }
          onTurnCompleteRef.current?.(id);
        }
      }
    },
    [clearLive],
  );

  // Resolve the human approval gate. POSTs { run_id, approved, reason? } to
  // /approve; the open SSE stream then delivers the run's continuation
  // (approval.granted → working, or approval.denied → declined). We never poll.
  const resolveApproval = useCallback(async (verdict: ApprovalResult) => {
    const id = streamIdRef.current ?? convIdRef.current;
    const tk = tokenRef.current;
    const runId =
      liveRunRef.current.approval?.run_id ??
      liveRunRef.current.runId ??
      runIdRef.current;
    if (!id || !tk || !runId) return;

    setLiveRun((s) => ({
      ...s,
      approvalResult: verdict,
      phase: verdict.approved ? "approving" : s.phase,
    }));

    try {
      await fetch(api(`/api/conversations/${id}/approve`), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${tk}`,
        },
        body: JSON.stringify({
          run_id: runId,
          approved: verdict.approved,
          ...(verdict.reason ? { reason: verdict.reason } : {}),
        }),
      });
    } catch (err) {
      setLiveRun((s) => ({
        ...s,
        phase: "error",
        errorMessage: `Approval failed to reach backend: ${(err as Error).message}`,
      }));
    }
  }, []);

  // Abort any live stream on unmount.
  useEffect(() => () => abortRef.current?.abort(), []);

  return {
    messages,
    loading,
    streaming,
    error,
    liveUser,
    liveAssistant,
    liveRun,
    send,
    resolveApproval,
  };
}
