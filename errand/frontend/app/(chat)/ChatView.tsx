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
import { usePathname } from "next/navigation";

import { api } from "@/lib/config";
import {
  useChatShell,
  newConversationId,
  conversationIdFromPath,
  shouldResetToNewChat,
  nonceRequestsReset,
} from "@/lib/chatShell";
import { useChat, type ConversationDetail } from "@/lib/useChat";
import { useVoiceAgent } from "@/lib/useVoiceAgent";
import type { RunState } from "@/lib/errandReducer";
import type { ModelOption, ProfileKind } from "@/lib/types";

import VoiceOrb, { type OrbPhase } from "@/components/VoiceOrb";
import { ErrandMark } from "@/components/Marks";
import ProfileToggle from "@/components/ProfileToggle";
import ModelSelector from "@/components/ModelSelector";
import Composer from "@/components/Composer";
import Thread from "@/components/chat/Thread";
import { AgentBubble } from "@/components/chat/bodies";
// The user-turn row/bubble treatment and the run phase→label map live with the
// memoized MessageRow (one source of truth); the live streaming turn below reuses
// them so a committed row and its live equivalent are pixel-identical.
import MessageRow, {
  userRow,
  userBubble,
  phaseLabel,
} from "@/components/chat/MessageRow";

import "./chat.anim.css";

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

export default function ChatView({
  initialId,
  initialDetail = null,
}: {
  initialId: string | null;
  initialDetail?: ConversationDetail | null;
}) {
  const shell = useChatShell();
  const convs = shell.conversations;
  const token = shell.token;

  const [models, setModels] = useState<ModelOption[]>(FALLBACK_MODELS);
  const [model, setModel] = useState(initialDetail?.model ?? "sol");
  const [profile, setProfile] = useState<ProfileKind>(initialDetail?.profile ?? "business");
  // The conversation this view is bound to. Seeded from the route; a first turn
  // mints one and pushes it into the URL without navigating.
  const [activeId, setActiveId] = useState<string | null>(initialId);
  const [intent, setIntent] = useState("");
  // "chat" = the persisted SSE text path (default). "voice" = the live relay,
  // an ephemeral transport layered over the same thread while the mic is open.
  const [mode, setMode] = useState<"chat" | "voice">("chat");

  const voice = useVoiceAgent(token);
  // The voice API changes identity across renders; a ref lets the reset effect
  // stop a live session without taking `voice` as a dependency (which would
  // re-run the effect every render and risk resetting mid-turn).
  const voiceRef = useRef(voice);
  voiceRef.current = voice;

  const activeIdRef = useRef<string | null>(activeId);
  activeIdRef.current = activeId;

  // ── Reset this reused instance to a blank new chat ──────────────────────────
  // A first turn claims its id with history.pushState (NOT a navigation) to keep
  // the SSE stream alive, so the App Router's rendered route stays /c while the
  // URL reads /c/<id>. That makes "New chat" (router.push("/c")) a no-op — the
  // same ChatView is reused with its old activeId, so nothing happens until a
  // refresh. The fix must not depend on the router.
  const resetToNewChat = useCallback(() => {
    // Stop a live voice session first — a blank chat must not keep a mic open or
    // keep streaming the old run's audio into a conversation that is now gone.
    if (voiceRef.current.active) voiceRef.current.stop();
    activeIdRef.current = null;
    setActiveId(null);
    setIntent("");
    setMode("chat");
    // activeId → null cascades into useChat (aborts any stream, clears messages)
    // and flips emptyThread back on, so the welcome state paints, not stale cards.
  }, []);

  // PRIMARY signal: the shell bumps newChatNonce every time "New chat" is pressed.
  // This is plain React state through context, so it fires regardless of whether
  // the router deduped the push. Skip the very first value (mount) so seeding a
  // real conversation is never wiped.
  const newChatNonce = shell.newChatNonce;
  const seenNonceRef = useRef(newChatNonce);
  useEffect(() => {
    if (!nonceRequestsReset(newChatNonce, seenNonceRef.current)) return;
    seenNonceRef.current = newChatNonce;
    resetToNewChat();
  }, [newChatNonce, resetToNewChat]);

  // BELT-AND-BRACES: also reset if the route genuinely flips to new-chat while we
  // still hold an id (e.g. browser back to /c). Harmless if the nonce already
  // handled it — the reset is idempotent. Kept because it covers history nav that
  // the button-driven nonce does not.
  const pathname = usePathname();
  const routeId = conversationIdFromPath(pathname);
  useEffect(() => {
    if (!shouldResetToNewChat(routeId, activeIdRef.current)) return;
    resetToNewChat();
  }, [routeId, resetToNewChat]);

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
    initialDetail,
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

  // Ensure this view is bound to a conversation id + URL, minting one if it is a
  // brand-new chat, and returning it. Both a first typed turn AND opening voice
  // call this, which is the whole fix for "typing after voice starts a new chat":
  // voice used to bind no id, so stopping it and typing left activeId null and
  // submit() minted a SECOND conversation. Now the id is claimed once, up front,
  // and the spoken run and the typed turn share it. Idempotent — a second call
  // returns the id already in place.
  //
  // pushState (not router.push) so an in-flight voice/SSE session is never torn
  // down by being given a URL; the rail row is inserted locally because the
  // server row is created lazily by the first turn that actually sends.
  const ensureConversation = useCallback((): string => {
    const existing = activeIdRef.current;
    if (existing) return existing;
    const id = newConversationId();
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
    return id;
  }, [convs, profile, model]);

  const submit = useCallback(async () => {
    if (composerLocked) return;
    const value = intent.trim();
    if (!value) return;
    setMode("chat");
    if (voice.active) voice.stop();
    setIntent("");

    const id = ensureConversation();
    await chat.send(value, { conversationId: id, profile, model });
  }, [composerLocked, intent, voice, ensureConversation, profile, model, chat]);

  // ── Voice mic (retained) ─────────────────────────────────────────────────
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const toggleMic = useCallback(() => {
    if (voice.active) {
      voice.stop();
      return;
    }
    if (streaming) return; // don't start voice mid text-turn
    // Claim/keep the conversation id BEFORE the mic opens, so a message typed
    // after this voice session continues THIS chat instead of minting a new one.
    ensureConversation();
    setMode("voice");
    void voice.start(model, profile);
  }, [voice, streaming, ensureConversation, model, profile]);

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
      <header className="flex flex-none items-center gap-[14px] px-[clamp(16px,3vw,24px)] py-3 shadow-[inset_0_-1px_0_var(--color-edge)]">
        <button
          className="hidden h-[38px] w-[38px] flex-none items-center justify-center rounded-[10px] border-none bg-ink-100 text-mid shadow-[inset_0_0_0_1px_var(--color-edge)] transition-[background-color,color] duration-[160ms] ease-[ease] hover:bg-ink-150 hover:text-hi [@media(max-width:860px)]:inline-flex"
          type="button"
          onClick={() => shell.setDrawerOpen(!shell.drawerOpen)}
          aria-label="Toggle conversation list"
        >
          <MenuGlyph />
        </button>

        <div className="inline-flex items-center gap-[9px] text-hi">
          <span className="inline-flex text-green">
            <ErrandMark size={20} />
          </span>
          <span className="font-display text-[19px] tracking-[0.02em]">Errand</span>
        </div>

        <div className="ml-auto flex items-center gap-3">
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
      <div
        className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden [scrollbar-color:var(--color-ink-250)_transparent] [scrollbar-width:thin]"
        ref={scrollRef}
      >
        <div className="mx-auto flex w-full max-w-[760px] flex-col gap-3 px-[clamp(16px,4vw,28px)] pt-[26px] pb-[30px]">
          {emptyThread ? (
            <div className="flex min-h-[calc(100dvh-320px)] flex-col items-center justify-center py-6 text-center">
              <span className="mb-[18px] inline-flex text-green">
                <ErrandMark size={46} />
              </span>
              {/* font-bold restores the UA heading weight the old stylesheet
                  relied on — Tailwind's preflight resets h1 to inherit. */}
              <h1 className="mx-0 mt-0 mb-[14px] max-w-[18ch] font-display text-[clamp(28px,5vw,42px)] leading-[1.1] font-bold tracking-[0.01em] text-balance text-hi">
                Tell it the errand. <em className="italic text-green-soft">You</em> approve
                the spend.
              </h1>
              <p className="mx-0 mt-0 mb-[28px] max-w-[50ch] text-[15px] leading-[1.55] text-mid">
                Errand shops an approved merchant, builds a cart against your
                policy, and pins a Prava card session — then waits for your
                passkey before a cent moves.
              </p>
              <div className="flex w-full max-w-[460px] flex-col gap-[10px]">
                {EXAMPLES[profile].map((ex) => (
                  <button
                    key={ex}
                    className="rounded-[13px] border-none bg-ink-100 px-4 py-[13px] text-left text-[13.5px] leading-[1.4] text-body shadow-[inset_0_0_0_1px_var(--color-edge)] transition-[background-color,color] duration-[180ms] ease-[ease] hover:bg-ink-150 hover:text-hi"
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
            <div className="flex w-full flex-col gap-[22px]">
              {/* History: rows loaded from the server, then rows committed from
                  turns taken in this session. Each row is a memoized MessageRow —
                  while a turn streams, ChatView re-renders every token but a
                  committed row's message reference is unchanged, so its memo skips
                  the re-render. Only the live bubble below updates per token. */}
              {chat.messages.map((m) => (
                <MessageRow
                  key={m.id}
                  message={m}
                  onResolveApproval={chat.resolveApproval}
                />
              ))}

              {/* Live streaming turn (before it is committed) */}
              {chat.liveUser !== null && (
                <div className={userRow}>
                  <div className={userBubble}>{chat.liveUser}</div>
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
                <div
                  className="rounded-card bg-ink-100 px-4 py-3 text-[13px] leading-[1.5] text-danger shadow-[inset_0_0_0_1px_rgba(255,122,107,0.22)]"
                  role="alert"
                >
                  {chat.error}
                </div>
              )}
            </div>
          )}

          {/* Voice hiccup / connection-lost banner */}
          {mode === "voice" && (voice.error || voice.state.connection === "lost") && (
            <div
              className="mt-4 flex items-center gap-[14px] rounded-card bg-[linear-gradient(180deg,var(--color-ink-150),var(--color-ink-100))] px-4 py-[14px] shadow-[inset_0_1px_0_rgba(232,180,95,0.14),inset_0_0_0_1px_var(--color-edge-strong)]"
              role="status"
            >
              <span className="inline-flex flex-none items-center justify-center text-brass">
                <LinkBreakGlyph />
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] [font-weight:640] text-hi">
                  {voice.error ? "Voice hiccup" : "Voice connection ended"}
                </div>
                <div className="mt-[2px] text-[12.5px] leading-[1.45] text-mid">
                  {voice.error ??
                    "The voice session closed. Everything said so far is preserved above."}
                </div>
              </div>
              <button
                className="flex-none rounded-[10px] border-none bg-brass px-4 py-[9px] text-[13px] [font-weight:640] text-[#1c1405] shadow-[inset_0_1px_0_rgba(255,255,255,0.28)] transition-[background-color] duration-[180ms] ease-[ease] hover:bg-[#f0c579]"
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
      <footer className="flex-none bg-[linear-gradient(180deg,rgba(7,11,9,0),var(--color-ink-000)_40%)] px-0 pt-3 pb-4 shadow-[inset_0_1px_0_var(--color-edge)]">
        <div className="mx-auto w-full max-w-[760px] px-[clamp(16px,4vw,28px)]">
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
          <div className="mt-[10px] text-center text-[11.5px] tracking-[0.01em] text-low">
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

// A quiet three-dot "assistant is typing" bubble, shown only before the first
// token / first tool card of a live turn — never hides real content.
function TypingBubble() {
  return (
    <div
      className="inline-flex w-fit items-center gap-[6px] rounded-panel bg-[linear-gradient(180deg,var(--color-ink-150),var(--color-ink-100))] px-[18px] py-[14px] shadow-[inset_0_1px_0_rgba(160,240,200,0.06),inset_0_0_0_1px_var(--color-edge)]"
      aria-label="Assistant is responding"
    >
      <span className="h-[6px] w-[6px] rounded-full bg-green-soft opacity-50 animate-[typingPulse_1.2s_ease-in-out_infinite]" />
      <span className="h-[6px] w-[6px] rounded-full bg-green-soft opacity-50 animate-[typingPulse_1.2s_ease-in-out_infinite] [animation-delay:0.15s]" />
      <span className="h-[6px] w-[6px] rounded-full bg-green-soft opacity-50 animate-[typingPulse_1.2s_ease-in-out_infinite] [animation-delay:0.3s]" />
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
