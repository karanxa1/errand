"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/config";
import { useErrandRun } from "@/lib/useErrandRun";
import { useVoice } from "@/lib/useVoice";
import type { ModelOption, ProfileKind } from "@/lib/types";
import { money } from "@/lib/format";

import VoiceOrb from "@/components/VoiceOrb";
import { ErrandMark, markFor } from "@/components/Marks";
import ProfileToggle from "@/components/ProfileToggle";
import ModelSelector from "@/components/ModelSelector";
import Composer from "@/components/Composer";
import PlanPanel from "@/components/stages/PlanPanel";
import CartPanel from "@/components/stages/CartPanel";
import ApprovalPanel from "@/components/stages/ApprovalPanel";
import ProgressPanel from "@/components/stages/ProgressPanel";
import DonePanel from "@/components/stages/DonePanel";
import AuditLog from "@/components/stages/AuditLog";

import css from "./page.module.css";

const FALLBACK_MODELS: ModelOption[] = [
  { key: "sol", label: "Sol", tagline: "Flagship — most capable", id: "gpt-5.6-sol" },
  { key: "terra", label: "Terra", tagline: "Balanced — everyday", id: "gpt-5.6-terra" },
  { key: "luna", label: "Luna", tagline: "Fastest — lightweight", id: "gpt-5.6-luna" },
];

const PRESETS: Record<ProfileKind, string> = {
  business: "Restock the office pantry, under $200, approved brands only.",
  personal: "Order this week's groceries — oat milk, dark roast, sparkling water.",
};

export default function Home() {
  const [models, setModels] = useState<ModelOption[]>(FALLBACK_MODELS);
  const [model, setModel] = useState("sol");
  const [profile, setProfile] = useState<ProfileKind>("business");
  const [intent, setIntent] = useState("");

  const { state, start, approve, reset } = useErrandRun();
  const running = state.phase !== "idle";

  // Gate browser-only capability text until after mount to avoid a hydration
  // mismatch (SpeechRecognition / getUserMedia don't exist during SSR).
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const voice = useVoice({
    onTranscript: (text) => setIntent(text),
  });

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

  const orbPhase = useMemo<
    "idle" | "listening" | "thinking" | "working" | "done" | "error"
  >(() => {
    if (state.phase === "error") return "error";
    if (state.phase === "done") return "done";
    if (voice.isListening) return "listening";
    if (["starting", "planning", "cart"].includes(state.phase)) return "thinking";
    if (["awaiting_approval", "approving", "working"].includes(state.phase))
      return "working";
    return "idle";
  }, [state.phase, voice.isListening]);

  const submit = () => {
    const text = intent.trim() || PRESETS[profile];
    if (voice.isListening) voice.stop();
    setIntent(text);
    start({ profile, intent: text, model });
  };

  const toggleMic = () => {
    if (voice.isListening) voice.stop();
    else void voice.start();
  };

  const flipProfile = (p: ProfileKind) => {
    setProfile(p);
    // If a run is finished, offer to re-run in the other profile from idle.
    if (running && (state.phase === "done" || state.phase === "error")) {
      reset();
      setIntent("");
    }
  };

  const statusText = () => {
    if (!mounted) return "Voice-first · or type below";
    if (voice.error) return voice.error;
    if (voice.isListening) return "Listening — speak your errand";
    if (voice.speechSupported) return "Voice ready · or type below";
    return "Type your errand below";
  };

  return (
    <div className={css.shell}>
      <header className={css.topbar}>
        <div className={css.brand}>
          <span className={css.brandMark}>
            <ErrandMark size={22} />
          </span>
          <span>
            <div className={css.brandName}>Errand</div>
            <div className={css.brandTag}>shops · pays · with your say-so</div>
          </span>
        </div>
        <div className={css.topRight}>
          <ProfileToggle
            value={profile}
            onChange={flipProfile}
            disabled={running && state.phase !== "done" && state.phase !== "error"}
          />
          <ModelSelector
            models={models}
            value={model}
            onChange={setModel}
            disabled={running && state.phase !== "done" && state.phase !== "error"}
          />
        </div>
      </header>

      {!running ? (
        <main className={css.idle}>
          <div className={css.orbHolder}>
            <VoiceOrb
              level={voice.level}
              band={voice.band}
              active={voice.isListening}
              phase={orbPhase}
              size={264}
            />
          </div>

          <div className={`${css.status} ${voice.isListening ? css.statusLive : ""}`}>
            {statusText()}
          </div>

          <h1 className={css.headline}>
            Tell it the errand.
            <br />
            <em>You</em> approve the spend.
          </h1>
          <p className={css.lede}>
            Errand shops an approved merchant, builds the cart against your
            policy, and pins a Prava card session — then waits for your passkey
            before a cent moves.
          </p>

          <div className={css.composerHolder}>
            <Composer
              value={intent}
              onChange={setIntent}
              onSubmit={submit}
              listening={voice.isListening}
              onToggleMic={toggleMic}
              micSupported={mounted && voice.supported}
              hint={
                voice.isListening
                  ? "Recording — the orb is reacting to your mic"
                  : undefined
              }
              error={voice.error}
            />
            <div className={css.metaRow}>
              <span>{profile === "business" ? "Procurement" : "Personal errands"}</span>
              <span className={css.metaSep} />
              <button
                className={css.tryBtn}
                onClick={() => setIntent(PRESETS[profile])}
              >
                Try: “{PRESETS[profile]}”
              </button>
            </div>
          </div>
        </main>
      ) : (
        <main className={css.run}>
          <aside className={css.rail}>
            <div className={css.railOrb}>
              <VoiceOrb
                level={voice.level}
                band={voice.band}
                active={voice.isListening}
                phase={orbPhase}
                size={140}
              />
              <div className={css.railPhase}>{phaseLabel(state.phase)}</div>
              <div className={css.railIntent}>{intent}</div>
            </div>

            <div className={css.railMeta}>
              <div className={css.railMetaRow}>
                <span className={css.railMetaKey}>Profile</span>
                <span className={css.railMetaVal}>
                  {profile === "business" ? "Business" : "Personal"}
                </span>
              </div>
              <div className={css.railMetaRow}>
                <span className={css.railMetaKey}>Model</span>
                <span className={css.railMetaVal}>
                  {models.find((m) => m.key === (state.model ?? model))?.label ??
                    model}
                </span>
              </div>
              {state.context && (
                <div className={css.railMetaRow}>
                  <span className={css.railMetaKey}>Budget</span>
                  <span className={css.railMetaVal}>
                    {money(state.context.budget_cents)}
                  </span>
                </div>
              )}
              {state.runId && (
                <div className={css.railMetaRow}>
                  <span className={css.railMetaKey}>Run</span>
                  <span className={css.railMetaVal}>
                    {state.runId.slice(0, 8)}
                  </span>
                </div>
              )}
            </div>

            <button
              className={css.restart}
              onClick={() => {
                reset();
                setIntent("");
              }}
            >
              <span style={{ color: "var(--green)" }}>{markFor(model, 15)}</span>
              New errand
            </button>
          </aside>

          <section className={css.stages}>
            {state.context && <PlanPanel context={state.context} />}
            {state.cart && (
              <CartPanel
                cart={state.cart}
                budgetCents={state.context?.budget_cents}
              />
            )}
            {state.phase === "awaiting_approval" && state.approval && (
              <ApprovalPanel
                approval={state.approval}
                onApprove={approve}
                approving={false}
              />
            )}
            {state.phase === "approving" && state.approval && (
              <ApprovalPanel
                approval={state.approval}
                onApprove={approve}
                approving={true}
              />
            )}
            {(state.phase === "working" ||
              state.phase === "done") && <ProgressPanel state={state} />}
            {(state.phase === "done" || state.phase === "error") && (
              <DonePanel state={state} />
            )}
            <AuditLog entries={state.audit} />
          </section>
        </main>
      )}
    </div>
  );
}

function phaseLabel(phase: string): string {
  switch (phase) {
    case "starting":
      return "Starting";
    case "planning":
      return "Grounding";
    case "cart":
      return "Building cart";
    case "awaiting_approval":
      return "Awaiting you";
    case "approving":
      return "Approving";
    case "working":
      return "Settling";
    case "done":
      return "Complete";
    case "error":
      return "Stopped";
    default:
      return "Idle";
  }
}
