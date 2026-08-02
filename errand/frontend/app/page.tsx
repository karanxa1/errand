"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/config";
import { useErrandRun } from "@/lib/useErrandRun";
import { useVoiceAgent } from "@/lib/useVoiceAgent";
import type { RunState } from "@/lib/errandReducer";
import type { ModelOption, ProfileKind } from "@/lib/types";

import VoiceOrb, { type OrbPhase } from "@/components/VoiceOrb";
import { ErrandMark } from "@/components/Marks";
import ProfileToggle from "@/components/ProfileToggle";
import ModelSelector from "@/components/ModelSelector";
import Composer from "@/components/Composer";
import Thread from "@/components/chat/Thread";
import AuditLog from "@/components/stages/AuditLog";

import css from "./page.module.css";

const FALLBACK_MODELS: ModelOption[] = [
  { key: "sol", label: "Sol", tagline: "Flagship — most capable", id: "gpt-5.6-sol" },
  { key: "terra", label: "Terra", tagline: "Balanced — everyday", id: "gpt-5.6-terra" },
  { key: "luna", label: "Luna", tagline: "Fastest — lightweight", id: "gpt-5.6-luna" },
];

// Example prompts per persona — clicking one fills the composer.
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

// Which transport is driving the thread. "text" = the SSE composer path;
// "voice" = the Deepgram relay (VoiceOrb). They never run at once; the thread
// renders whichever is active, from the SAME reducer + cards.
type Mode = "text" | "voice";

export default function Home() {
  const [models, setModels] = useState<ModelOption[]>(FALLBACK_MODELS);
  const [model, setModel] = useState("sol");
  const [profile, setProfile] = useState<ProfileKind>("business");
  const [intent, setIntent] = useState("");
  // The intent actually sent for the current text run (drives the user bubble).
  const [submittedIntent, setSubmittedIntent] = useState("");
  const [showAudit, setShowAudit] = useState(false);
  const [mode, setMode] = useState<Mode>("text");

  const text = useErrandRun();
  const voice = useVoiceAgent();

  // The run state the thread renders comes from the active transport.
  const state: RunState = mode === "voice" ? voice.state : text.state;

  const textRunning = text.state.phase !== "idle";
  const textLocked =
    textRunning &&
    text.state.phase !== "done" &&
    text.state.phase !== "error" &&
    text.state.phase !== "declined";

  // The composer is locked while a text run is mid-flight OR while voice is live
  // (so the two transports can't run in parallel).
  const locked = textLocked || voice.active;
  // An errand step is genuinely in flight (not merely idle voice listening) —
  // drives the "you'll be asked to approve" dock note.
  const midFlight =
    state.phase !== "idle" &&
    state.phase !== "done" &&
    state.phase !== "error" &&
    state.phase !== "declined";
  // The thread is shown once either transport has anything to say.
  const running =
    mode === "voice"
      ? voice.active ||
        voice.state.connection === "connecting" ||
        state.audit.length > 0
      : textRunning;

  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Load model options (public endpoint). Falls back silently to defaults.
  useEffect(() => {
    let alive = true;
    fetch(api("/api/models"))
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!alive || !data?.models) return;
        setModels(data.models);
        if (data.default) setModel(data.default);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const orbPhase = useMemo<OrbPhase>(() => {
    // Voice mode: the orb tracks the conversational phase + connection health.
    if (mode === "voice") {
      if (voice.state.connection === "lost") return "reconnecting";
      if (voice.voicePhase === "listening") return "listening";
      if (voice.voicePhase === "thinking") return "thinking";
      if (voice.voicePhase === "speaking") return "working";
      if (voice.active) return "listening";
      return "idle";
    }
    // Text mode: the orb tracks the SSE run phase.
    if (state.connection === "reconnecting") return "reconnecting";
    if (state.connection === "lost" && state.phase !== "error") return "reconnecting";
    if (state.phase === "error") return "error";
    if (state.phase === "done") return "done";
    if (state.phase === "declined") return "idle";
    if (["starting", "planning", "cart"].includes(state.phase)) return "thinking";
    if (["awaiting_approval", "approving", "working"].includes(state.phase)) return "working";
    return "idle";
  }, [mode, voice.voicePhase, voice.active, voice.state.connection, state.phase, state.connection]);

  const resolveApproval = mode === "voice" ? voice.resolveApproval : text.resolveApproval;

  // Auto-stick-to-bottom while streaming: scroll to the newest content whenever
  // the audit grows or the phase changes, but only if the user is already near
  // the bottom (so scrolling up to read history isn't yanked away).
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 180;
    if (nearBottom) {
      bottomRef.current?.scrollIntoView({
        behavior: state.audit.length <= 1 ? "auto" : "smooth",
        block: "end",
      });
    }
  }, [state.audit.length, state.phase, state.connection]);

  const submit = () => {
    if (locked) return;
    const value = intent.trim() || EXAMPLES[profile][0];
    setMode("text");
    setIntent(value);
    setSubmittedIntent(value);
    text.start({ profile, intent: value, model });
  };

  // The VoiceOrb IS the talk button: tap to open the relay + mic, tap to stop.
  const toggleMic = () => {
    if (voice.active) {
      voice.stop();
      return;
    }
    // Don't start voice while a text run is mid-flight.
    if (textLocked) return;
    setMode("voice");
    void voice.start(model, profile);
  };

  const newErrand = () => {
    if (voice.active) voice.stop();
    text.reset();
    setMode("text");
    setIntent("");
    setSubmittedIntent("");
  };

  const retry = () => {
    if (mode === "voice") {
      void voice.start(model, profile);
    } else {
      text.retry();
    }
  };

  const flipProfile = (p: ProfileKind) => {
    setProfile(p);
    if (running && !locked) {
      if (voice.active) voice.stop();
      text.reset();
      setMode("text");
      setIntent("");
      setSubmittedIntent("");
    }
  };

  const voiceStateLabel =
    voice.voicePhase === "listening"
      ? "Listening"
      : voice.voicePhase === "thinking"
        ? "Thinking"
        : voice.voicePhase === "speaking"
          ? "Speaking"
          : "Connected";

  const composerHint = voice.active
    ? `${voiceStateLabel} — tap the orb to end`
    : !mounted
      ? undefined
      : locked
        ? "Errand in flight — I'll stream each step below"
        : voice.supported
          ? "Tap the orb to talk · or type · Enter to send"
          : "Type your errand · Enter to send";

  const orbControl = (
    <VoiceOrb
      level={voice.level}
      band={voice.band}
      active={voice.active}
      phase={orbPhase}
      size={44}
    />
  );

  return (
    <div className={css.shell}>
      <header className={css.topbar}>
        <div className={css.brand}>
          <span className={css.brandMark}>
            <ErrandMark size={22} />
          </span>
          <span className={css.brandText}>
            <span className={css.brandName}>Errand</span>
            <span className={css.brandTag}>shops · pays · with your say-so</span>
          </span>
        </div>

        <div className={css.topRight}>
          {running && (
            <button className={css.ghostBtn} onClick={newErrand} type="button">
              <PlusGlyph />
              New errand
            </button>
          )}
          {state.audit.length > 0 && (
            <button
              className={`${css.ghostBtn} ${showAudit ? css.ghostBtnOn : ""}`}
              onClick={() => setShowAudit((v) => !v)}
              type="button"
              aria-pressed={showAudit}
            >
              <LedgerGlyph />
              {showAudit ? "Hide raw audit" : "Raw audit"}
            </button>
          )}
        </div>
      </header>

      <div className={css.threadScroll} ref={scrollRef}>
        <div className={css.col}>
          {!running ? (
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
          ) : (
            <>
              <Thread
                intent={mode === "voice" ? undefined : submittedIntent}
                state={state}
                phaseLabel={phaseLabel(state.phase)}
                onResolveApproval={resolveApproval}
                interim={mode === "voice" ? voice.interim : undefined}
              />

              {/* Voice mode owns its own banner: one row, the reason + a retry
                  that opens a fresh session. It supersedes the SSE-flavored
                  connection-lost banner below. */}
              {mode === "voice" &&
                (voice.error || state.connection === "lost") && (
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
                    <button className={css.lostRetry} onClick={retry} type="button">
                      Talk again
                    </button>
                  </div>
                )}

              {mode !== "voice" &&
                state.connection === "lost" &&
                state.phase !== "error" && (
                <div className={css.lostBanner} role="status">
                  <span className={css.lostMark}>
                    <LinkBreakGlyph />
                  </span>
                  <div className={css.lostBody}>
                    <div className={css.lostTitle}>Connection lost</div>
                    <div className={css.lostText}>
                      The live stream to this run dropped. Every step received so
                      far is preserved above. Reconnecting can&apos;t resume the
                      same run — retry starts a fresh errand.
                    </div>
                  </div>
                  <button className={css.lostRetry} onClick={retry} type="button">
                    Retry errand
                  </button>
                </div>
              )}

              {showAudit && (
                <div className={css.auditHolder}>
                  <AuditLog entries={state.audit} />
                </div>
              )}
            </>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <footer className={css.dock}>
        <div className={css.col}>
          <div className={css.controls}>
            <ProfileToggle value={profile} onChange={flipProfile} disabled={locked} />
            <ModelSelector
              models={models}
              value={model}
              onChange={setModel}
              disabled={locked}
            />
          </div>
          <Composer
            value={intent}
            onChange={setIntent}
            onSubmit={submit}
            disabled={textLocked}
            listening={voice.active}
            onToggleMic={toggleMic}
            micSupported={mounted && voice.supported}
            micDisabled={textLocked && !voice.active}
            micSlot={orbControl}
            hint={composerHint}
            // Voice errors are owned by the banner in the thread (with a retry);
            // don't also echo them under the composer as red text.
            error={null}
          />
          <div className={css.dockNote}>
            {midFlight
              ? "Working — you'll be asked to approve before anything is charged."
              : "Nothing is charged until you approve with a passkey."}
          </div>
        </div>
      </footer>
    </div>
  );
}

function phaseLabel(phase: string): string {
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

function PlusGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M8 3.2v9.6M3.2 8h9.6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}

function LedgerGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="3" y="2.5" width="10" height="11" rx="1.6" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M5.5 6h5M5.5 8.5h5M5.5 11h3"
        stroke="currentColor"
        strokeWidth="1.4"
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
