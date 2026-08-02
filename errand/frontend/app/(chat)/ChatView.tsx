"use client";

/* One conversation: the top bar (model + profile controls), the thread, and the
 * composer. Mounted under the persistent shell, so navigating between chats
 * replaces only this — the rail above it never unmounts.
 *
 * A brand-new chat arrives here with `initialId = null`. It does not exist
 * anywhere yet: no row, no URL. The first turn mints an id, writes it into the
 * address bar with window.history.pushState, and streams. pushState is not a
 * navigation — the App Router picks up the new pathname for usePathname without
 * re-rendering the route — so the component that is holding the open SSE reader
 * stays mounted and the stream survives being given a URL. router.push here
 * would tear this subtree down mid-token. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/config";
import { useChatShell, newConversationId } from "@/lib/chatShell";
import { useChat, runStateFromEvents, type ConversationDetail } from "@/lib/useChat";
import { useVoiceAgent } from "@/lib/useVoiceAgent";
import type { RunState, RunPhase } from "@/lib/errandReducer";
import type { ModelOption, ProfileKind } from "@/lib/types";

import VoiceOrb, { type OrbPhase } from "@/components/VoiceOrb";
import { ErrandMark } from "@/components/Marks";
import ProfileToggle from "@/components/ProfileToggle";
import ModelSelector from "@/components/ModelSelector";
import Composer from "@/components/Composer";
import Thread from "@/components/chat/Thread";
import { AgentBubble } from "@/components/chat/bodies";

import css from "./chat.module.css";
import t from "@/components/chat/Thread.module.css";

const FALLBACK_MODELS: ModelOption[] = [
  { key: "sol", label: "Sol", tagline: "Flagship — most capable", id: "gpt-5.6-sol" },
  { key: "terra", label: "Terra", tagline: "Balanced — everyday", id: "gpt-5.6-terra" },
  { key: "luna", label: "Luna", tagline: "Fastest — lightweight", id: "gpt-5.6-luna" },
];

const EXAMPLES: Record<ProfileKind, string[]> = {
  business: [
    "Restock the office pantry under $200, approved brands only.",
    "Order 3 boxes of nitrile gloves for the lab, size M.",
    "Reorder our usual printer toner before we run out.",
  ],
  personal: [
    "Order this week's groceries — oat milk, dark roast, sparkling water.",
    "Grab a birthday card and a small houseplant, under $40.",
    "Restock the dog food we bought last month.",
  ],
};

export default function ChatView({ initialId }: { initialId: string | null }) {
  const shell = useChatShell();
  const convs = shell.conversations;
  const token = shell.token;

  const [models, setModels] = useState<ModelOption[]>(FALLBACK_MODELS);
  const [model, setModel] = useState("sol");
  const [profile, setProfile] = useState<ProfileKind>("business");
  // The conversation this view is bound to. Seeded from the route; a first turn
  // mints one and pushes it into the URL without navigating.
  const [activeId, setActiveId] = useState<string | null>(initialId);
  const [intent, setIntent] = useState("");
  // "chat" = the persisted SSE text path (default). "voice" = the live relay,
  // an ephemeral transport layered over the same thread while the mic is open.
  const [mode, setMode] = useState<"chat" | "voice">("chat");

  const voice = useVoiceAgent(token);

  const activeIdRef = useRef<string | null>(activeId);
  activeIdRef.current = activeId;

  // Sync model/profile from a loaded conversation so the top bar reflects it.
  const onLoaded = useCallback((detail: ConversationDetail) => {
    if (detail.id !== activeIdRef.current) return;
    if (detail.model) setModel(detail.model);
    if (detail.profile) setProfile(detail.profile);
  }, []);

  const onTitle = useCallback(
    (id: string, title: string) => convs.setTitle(id, title),
    [convs],
  );

  // A finished turn moves its conversation to the top of the rail. That is a
  // local reorder of a list we already hold — re-pulling the whole list from the
  // server to learn an ordering we can compute would be a round-trip for nothing.
  const onTurnComplete = useCallback((id: string) => convs.bump(id), [convs]);

  const chat = useChat({
    conversationId: activeId,
    token,
    onTitle,
    onTurnComplete,
    onLoaded,
  });

  // Load model options (public). Falls back silently to defaults.
  useEffect(() => {
    let alive = true;
    fetch(api("/api/models"))
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!alive || !data?.models) return;
        setModels(data.models);
        // Only adopt the server default when nothing is selected yet.
        if (data.default) setModel((m) => m || data.default);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // ── Top-bar control changes → persist onto the active conversation ─────────
  // Only PATCH a conversation that exists server-side. A new chat has an id and a
  // URL but no row until its first turn creates one, and that turn carries the
  // current profile/model with it — so changing them beforehand is already
  // recorded, without a request that would 404.
  const persistChange = useCallback(
    (changes: { profile?: ProfileKind; model?: string }) => {
      const id = activeIdRef.current;
      if (id && convs.has(id)) void convs.patch(id, changes);
    },
    [convs],
  );

  const changeModel = useCallback(
    (key: string) => {
      setModel(key);
      persistChange({ model: key });
    },
    [persistChange],
  );

  const changeProfile = useCallback(
    (p: ProfileKind) => {
      setProfile(p);
      persistChange({ profile: p });
    },
    [persistChange],
  );

  // ── Sending ────────────────────────────────────────────────────────────────
  const streaming = chat.streaming;
  const voiceLive = voice.active;
  // Textarea + send lock while a chat turn streams OR voice is live. The mic
  // stays tappable (to STOP voice) even when the composer is otherwise locked.
  const composerLocked = streaming || voiceLive;

  const submit = useCallback(async () => {
    if (composerLocked) return;
    const value = intent.trim();
    if (!value) return;
    setMode("chat");
    if (voice.active) voice.stop();
    setIntent("");

    let id = activeIdRef.current;
    if (!id) {
      // Claim an id, put it in the address bar, and start. No round-trip: the
      // row is created server-side by this very turn.
      id = newConversationId();
      activeIdRef.current = id;
      setActiveId(id);
      window.history.pushState({}, "", `/c/${id}`);
      const now = new Date().toISOString();
      convs.insert({
        id,
        title: "New chat",
        profile,
        model,
        created_at: now,
        updated_at: now,
      });
    }
    await chat.send(value, { conversationId: id, profile, model });
  }, [composerLocked, intent, voice, convs, profile, model, chat]);

  // ── Voice mic (retained) ─────────────────────────────────────────────────
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const toggleMic = useCallback(() => {
    if (voice.active) {
      voice.stop();
      return;
    }
    if (streaming) return; // don't start voice mid text-turn
    setMode("voice");
    void voice.start(model, profile);
  }, [voice, streaming, model, profile]);

  // ── Which RunState + resolver the thread is currently showing ──────────────
  // In voice mode the live voice RunState + its WS resolver drive the thread;
  // in chat mode the persisted turn's chat resolver does.
  const voiceThreadActive =
    mode === "voice" &&
    (voice.active ||
      voice.state.connection === "connecting" ||
      voice.state.audit.length > 0);

  const resolveApproval = voiceThreadActive
    ? voice.resolveApproval
    : chat.resolveApproval;

  // ── Orb phase ──────────────────────────────────────────────────────────────
  const orbPhase = useMemo<OrbPhase>(() => {
    if (mode === "voice") {
      if (voice.state.connection === "lost") return "reconnecting";
      if (voice.voicePhase === "listening") return "listening";
      if (voice.voicePhase === "thinking") return "thinking";
      if (voice.voicePhase === "speaking") return "working";
      if (voice.active) return "listening";
      return "idle";
    }
    // Chat mode: the orb tracks the streaming text turn's run.
    const r = chat.liveRun;
    if (chat.streaming) {
      if (r.phase === "awaiting_approval") return "working";
      if (["starting", "planning", "cart"].includes(r.phase)) return "thinking";
      if (["approving", "working"].includes(r.phase)) return "working";
      return "thinking";
    }
    return "idle";
  }, [mode, voice.voicePhase, voice.active, voice.state.connection, chat.liveRun, chat.streaming]);

  const orbControl = (
    <VoiceOrb
      level={voice.level}
      band={voice.band}
      active={voice.active}
      phase={orbPhase}
      size={44}
    />
  );

  // ── Auto-stick-to-bottom while streaming ────────────────────────────────────
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 220;
    if (nearBottom) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [
    chat.messages.length,
    chat.liveAssistant,
    chat.liveRun.audit.length,
    voice.state.audit.length,
    chat.streaming,
  ]);

  const composerHint = voice.active
    ? `${voiceStateLabel(voice.voicePhase)} — tap the orb to end`
    : !mounted
      ? undefined
      : streaming
        ? "Working — I'll stream each step below"
        : voice.supported
          ? "Tap the orb to talk · or type · Enter to send"
          : "Type your message · Enter to send";

  // The persisted thread has no messages AND nothing is streaming/voicing.
  const emptyThread =
    chat.messages.length === 0 &&
    !chat.streaming &&
    chat.liveUser === null &&
    !voiceThreadActive;

  return (
    <>
      {/* ── Top bar: brand + the model selector and profile toggle (at the TOP,
          per the brief) ─────────────────────────────────────────────────────── */}
      <header className={css.topbar}>
        <button
          className={css.menuBtn}
          type="button"
          onClick={() => shell.setDrawerOpen(!shell.drawerOpen)}
          aria-label="Toggle conversation list"
        >
          <MenuGlyph />
        </button>

        <div className={css.brand}>
          <span className={css.brandMark}>
            <ErrandMark size={20} />
          </span>
          <span className={css.brandName}>Errand</span>
        </div>

        <div className={css.controls}>
          <ProfileToggle value={profile} onChange={changeProfile} disabled={composerLocked} />
          <ModelSelector
            models={models}
            value={model}
            onChange={changeModel}
            disabled={composerLocked}
          />
        </div>
      </header>

      {/* ── Thread ────────────────────────────────────────────────────────── */}
      <div className={css.threadScroll} ref={scrollRef}>
        <div className={css.col}>
          {emptyThread ? (
            <div className={css.empty}>
              <span className={css.emptyMark}>
                <ErrandMark size={46} />
              </span>
              <h1 className={css.emptyTitle}>
                Tell it the errand. <em>You</em> approve the spend.
              </h1>
              <p className={css.emptyLede}>
                Errand shops an approved merchant, builds a cart against your
                policy, and pins a Prava card session — then waits for your
                passkey before a cent moves.
              </p>
              <div className={css.chips}>
                {EXAMPLES[profile].map((ex) => (
                  <button
                    key={ex}
                    className={css.chip}
                    type="button"
                    onClick={() => setIntent(ex)}
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          ) : voiceThreadActive ? (
            // Voice mode: the live voice transcript + cards.
            <Thread
              state={voice.state}
              phaseLabel={phaseLabel(voice.state.phase)}
              onResolveApproval={resolveApproval}
              interim={voice.interim}
            />
          ) : (
            <div className={t.thread}>
              {/* History: rows loaded from the server, then rows committed from
                  turns taken in this session. */}
              {chat.messages.map((m) => {
                if (m.role === "user") {
                  return (
                    <div key={m.id} className={t.userRow}>
                      <div className={t.userBubble}>{m.content}</div>
                    </div>
                  );
                }
                if (m.role === "assistant") {
                  return m.content.trim() ? (
                    <AgentBubble key={m.id} text={m.content} />
                  ) : null;
                }
                // tool: a locally-committed turn already carries the finished
                // RunState; a server-loaded one carries the frames to replay.
                const runState = m.runState ?? runStateFromEvents(m.events);
                if (runState.audit.length === 0) return null;
                return (
                  <Thread
                    key={m.id}
                    state={runState}
                    phaseLabel={phaseLabel(runState.phase)}
                    onResolveApproval={chat.resolveApproval}
                  />
                );
              })}

              {/* Live streaming turn (before it is committed) */}
              {chat.liveUser !== null && (
                <div className={t.userRow}>
                  <div className={t.userBubble}>{chat.liveUser}</div>
                </div>
              )}
              {chat.liveRun.audit.length > 0 && (
                <Thread
                  state={chat.liveRun}
                  phaseLabel={phaseLabel(chat.liveRun.phase)}
                  onResolveApproval={chat.resolveApproval}
                />
              )}
              {chat.liveAssistant.trim() ? (
                <AgentBubble text={chat.liveAssistant} />
              ) : (
                chat.streaming &&
                chat.liveRun.audit.length === 0 && <TypingBubble />
              )}

              {chat.error && (
                <div className={css.errorRow} role="alert">
                  {chat.error}
                </div>
              )}
            </div>
          )}

          {/* Voice hiccup / connection-lost banner */}
          {mode === "voice" && (voice.error || voice.state.connection === "lost") && (
            <div className={css.lostBanner} role="status">
              <span className={css.lostMark}>
                <LinkBreakGlyph />
              </span>
              <div className={css.lostBody}>
                <div className={css.lostTitle}>
                  {voice.error ? "Voice hiccup" : "Voice connection ended"}
                </div>
                <div className={css.lostText}>
                  {voice.error ??
                    "The voice session closed. Everything said so far is preserved above."}
                </div>
              </div>
              <button
                className={css.lostRetry}
                onClick={() => void voice.start(model, profile)}
                type="button"
              >
                Talk again
              </button>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* ── Composer dock ─────────────────────────────────────────────────── */}
      <footer className={css.dock}>
        <div className={css.col}>
          <Composer
            value={intent}
            onChange={setIntent}
            onSubmit={submit}
            disabled={composerLocked}
            listening={voice.active}
            onToggleMic={toggleMic}
            micSupported={mounted && voice.supported}
            micDisabled={streaming && !voice.active}
            micSlot={orbControl}
            hint={composerHint}
            error={null}
          />
          <div className={css.dockNote}>
            {chatMidFlight(chat.liveRun, chat.streaming)
              ? "Working — you'll be asked to approve before anything is charged."
              : "Nothing is charged until you approve with a passkey."}
          </div>
        </div>
      </footer>
    </>
  );
}

function chatMidFlight(run: RunState, streaming: boolean): boolean {
  if (!streaming) return false;
  return (
    run.phase !== "idle" &&
    run.phase !== "done" &&
    run.phase !== "error" &&
    run.phase !== "declined"
  );
}

function voiceStateLabel(phase: string): string {
  return phase === "listening"
    ? "Listening"
    : phase === "thinking"
      ? "Thinking"
      : phase === "speaking"
        ? "Speaking"
        : "Connected";
}

function phaseLabel(phase: RunPhase | string): string {
  switch (phase) {
    case "starting":
      return "Starting the run";
    case "planning":
      return "Grounding against policy";
    case "cart":
      return "Building the cart";
    case "awaiting_approval":
      return "Waiting for your approval";
    case "approving":
      return "Confirming the passkey";
    case "working":
      return "Settling the order";
    case "done":
      return "Complete";
    case "declined":
      return "Declined";
    case "error":
      return "Stopped";
    default:
      return "Working";
  }
}

// A quiet three-dot "assistant is typing" bubble, shown only before the first
// token / first tool card of a live turn — never hides real content.
function TypingBubble() {
  return (
    <div className={css.typing} aria-label="Assistant is responding">
      <span className={css.typingDot} />
      <span className={css.typingDot} />
      <span className={css.typingDot} />
    </div>
  );
}

function MenuGlyph() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path
        d="M3 5h12M3 9h12M3 13h12"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}

// A link with a break in it — the connection-lost mark, in the brand stroke.
function LinkBreakGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path
        d="M7 5.5 5 5.5A3 3 0 0 0 5 11.5H7"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
      <path
        d="M11 5.5 13 5.5A3 3 0 0 1 13 11.5H11"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
      <path
        d="M9 3.4 9 5.1M9 11.9 9 13.6"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}
