// useVoiceAgent — the browser side of the Errand Voice Relay (docs/api-reference
// "Errand Voice Relay + Tool Bridge — INTERNAL CONTRACT").
//
// Deepgram browser tokens are FORBIDDEN on our key, so the BACKEND holds the
// Deepgram Voice Agent WS and relays. The browser talks only to OUR backend WS:
//
//   ws://<backend>/api/voice/ws?model=<sol|terra|luna>&profile=<business|personal>
//                              &ticket=<one-shot>
//
// The relay spends real money (Deepgram + OpenAI credits, and run_errand can
// reach checkout), so it is authenticated. A WebSocket cannot carry an
// Authorization header, so start() first POSTs /api/voice/ticket with the bearer
// token and passes the opaque, single-use, 60s ticket it gets back in the query
// string. Backend side: app/voice/tickets.py. A missing/stale/replayed ticket
// closes the socket with 4401, which this hook reports as a sign-in problem
// rather than as a dropped connection.
//
//   browser → backend
//     · binary  : mic PCM (linear16, 48 kHz mono), captured via Web Audio.
//     · JSON    : {type:"start"} | {type:"stop"} |
//                 {type:"approve", run_id, approved, reason?}
//   backend → browser
//     · binary  : agent TTS PCM (linear16, 16 kHz) → queued + played.
//     · JSON    : voice.state / voice.user_transcript / voice.agent_transcript /
//                 tool.call / tool.result / websearch.result / voice.error, PLUS
//                 every errand event (run.started … run.done). All errand + tool
//                 + turn frames are folded through the SHARED reducer
//                 (lib/errandReducer.applyFrame), so a voice-driven run renders
//                 the SAME chat-thread cards as a typed run.
//
// The signature VoiceOrb reads `level` + `band` (real mic amplitude from an
// AnalyserNode) and `voicePhase` for its state→motion, exactly as before — the
// orb is untouched, only its data source is now this relay.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, wsApi } from "./config";
import {
  applyFrame,
  initialRunState,
  pushAuditEntry,
  type RunState,
} from "./errandReducer";
import type { ApprovalResult, RawFrame } from "./types";

export type VoicePhase = "idle" | "listening" | "thinking" | "speaking";

export interface VoiceAgentApi {
  state: RunState;
  // Whether a voice session (WS + mic) is currently open.
  active: boolean;
  // The Deepgram-driven conversational phase (drives the orb + a live label).
  voicePhase: VoicePhase;
  // 0..1 smoothed mic amplitude, and 5 frequency bands — the orb's real fuel.
  level: number;
  band: Float32Array;
  // The in-flight (not-yet-final) user utterance, shown as a forming bubble.
  interim: string;
  supported: boolean;
  error: string | null;
  start: (model: string, profile: string) => Promise<void>;
  stop: () => void;
  // Resolve an approval gate over the voice WS control channel (NOT the SSE
  // /approve POST). Approve mounts the Prava iframe in the card; either verdict
  // sends {type:"approve", run_id, approved, reason?}.
  resolveApproval: (verdict: ApprovalResult) => void;
}

const BANDS = 5;
const MIC_SAMPLE_RATE = 48000; // linear16 mic per Deepgram Settings.input
const TTS_SAMPLE_RATE = 16000; // linear16 agent audio per Settings.output

// Application close code the relay uses for "your ticket was missing, stale or
// already spent" — distinct from 1008 so an auth failure never reads as a
// generic policy close or a network blip.
const WS_UNAUTHORIZED = 4401;
const SIGNED_OUT_MESSAGE = "Voice needs you to be signed in.";

// `token` is optional so a call site that has not resolved auth yet still
// compiles; without one the mint below fails and voice never opens a socket.
export function useVoiceAgent(token?: string | null): VoiceAgentApi {
  const [state, setState] = useState<RunState>(initialRunState);
  const [active, setActive] = useState(false);
  const [voicePhase, setVoicePhase] = useState<VoicePhase>("idle");
  const [level, setLevel] = useState(0);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);
  const bandRef = useRef<Float32Array>(new Float32Array(BANDS));
  const [, force] = useState(0);

  // Web Audio + WS handles.
  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const micSinkRef = useRef<GainNode | null>(null);
  const playGainRef = useRef<GainNode | null>(null);
  const rafRef = useRef<number | null>(null);
  // Playback scheduling cursor (seconds, in the audio context clock).
  const playCursorRef = useRef(0);
  // Every TTS chunk scheduled but not yet finished. Web Audio has no "stop
  // everything" call — a BufferSource that has been start()ed will play to the
  // end unless it is individually stopped — so barge-in needs the handles.
  const scheduledRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  // The active run_id an approval belongs to (set by approval.request).
  const approvalRunIdRef = useRef<string | null>(null);
  // Guard against a manual stop being reported as a lost connection.
  const stoppingRef = useRef(false);
  // Monotonic session generation. Every start/stop invalidates callbacks and
  // permission requests belonging to an older session, so retries can never
  // resurrect or overlap a stale microphone/WebSocket.
  const sessionIdRef = useRef(0);

  const supported =
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    (typeof AudioContext !== "undefined" ||
      typeof (window as unknown as { webkitAudioContext?: unknown })
        .webkitAudioContext !== "undefined");

  // ── orb amplitude loop (real mic → level + bands) ───────────────────────────
  const tick = useCallback(() => {
    const analyser = analyserRef.current;
    if (!analyser) return;
    const bins = analyser.frequencyBinCount;
    const freq = new Uint8Array(bins);
    analyser.getByteFrequencyData(freq);

    let sum = 0;
    const lo = 2;
    const hi = Math.min(bins, Math.floor(bins * 0.6));
    for (let i = lo; i < hi; i++) sum += freq[i];
    const avg = sum / (hi - lo) / 255;
    setLevel((prev) => prev + (avg - prev) * 0.35);

    const band = bandRef.current;
    const step = Math.floor((hi - lo) / BANDS);
    for (let b = 0; b < BANDS; b++) {
      let s = 0;
      const from = lo + b * step;
      const to = b === BANDS - 1 ? hi : from + step;
      for (let i = from; i < to; i++) s += freq[i];
      const v = s / (to - from) / 255;
      band[b] = band[b] + (v - band[b]) * 0.4;
    }
    force((n) => (n + 1) % 1000000);
    rafRef.current = requestAnimationFrame(tick);
  }, []);

  // ── playback: 16 kHz linear16 mono → AudioBuffer, scheduled gapless ─────────
  const playPcm = useCallback((buf: ArrayBuffer) => {
    const ctx = audioCtxRef.current;
    const gain = playGainRef.current;
    if (!ctx || !gain || buf.byteLength === 0) return;
    const pcm = new Int16Array(buf);
    const frames = pcm.length;
    if (frames === 0) return;
    const audioBuf = ctx.createBuffer(1, frames, TTS_SAMPLE_RATE);
    const ch = audioBuf.getChannelData(0);
    for (let i = 0; i < frames; i++) ch[i] = pcm[i] / 32768;
    const src = ctx.createBufferSource();
    src.buffer = audioBuf;
    src.connect(gain);
    const now = ctx.currentTime;
    // Keep a small lead so consecutive chunks butt together without gaps.
    const startAt = Math.max(now + 0.02, playCursorRef.current);
    src.start(startAt);
    playCursorRef.current = startAt + audioBuf.duration;
    scheduledRef.current.add(src);
    src.onended = () => {
      scheduledRef.current.delete(src);
    };
  }, []);

  // Barge-in. Deepgram's message flow is explicit — "User began talking. Stop any
  // audio playback immediately" — and by the time that arrives, seconds of agent
  // speech are already scheduled ahead on the Web Audio timeline. Dropping the
  // socket's future chunks is not enough; the queue itself has to go, or the
  // agent keeps talking over the person who interrupted it.
  const flushPlayback = useCallback(() => {
    for (const src of scheduledRef.current) {
      try {
        src.stop();
      } catch {
        /* already ended between the send and this tick */
      }
    }
    scheduledRef.current.clear();
    // Re-anchor the cursor to now, so the next chunk starts immediately rather
    // than waiting out the silence where the cancelled audio would have been.
    const ctx = audioCtxRef.current;
    playCursorRef.current = ctx ? ctx.currentTime : 0;
  }, []);

  // ── JSON event routing ──────────────────────────────────────────────────────
  const handleEvent = useCallback((msg: Record<string, unknown>) => {
    const type = (msg.type as string) ?? "";

    switch (type) {
      case "voice.clear_audio": {
        flushPlayback();
        return;
      }
      case "voice.state": {
        const st = (msg.state as string) ?? "idle";
        setVoicePhase(
          st === "listening" || st === "thinking" || st === "speaking"
            ? (st as VoicePhase)
            : "idle",
        );
        return;
      }
      case "voice.user_transcript": {
        const text = ((msg.text as string) ?? "").trim();
        const isFinal = msg.is_final !== false;
        if (isFinal && text) {
          setInterim("");
          setState((s) => applyFrame(s, { event: "user.message", data: { text } }));
        } else {
          setInterim(text);
        }
        return;
      }
      case "voice.agent_transcript": {
        const text = ((msg.text as string) ?? "").trim();
        if (text) {
          setState((s) => applyFrame(s, { event: "agent.message", data: { text } }));
        }
        return;
      }
      case "voice.error": {
        const message = (msg.message as string) ?? "Voice error.";
        setError(message);
        setState((s) =>
          pushAuditEntry(s, new Date().toISOString(), "voice.error", message, { message }),
        );
        // A Deepgram Error frame ends this session. Closing here prevents a
        // failed-but-still-open relay from surviving behind the retry button.
        try {
          wsRef.current?.close();
        } catch {
          /* noop */
        }
        return;
      }
      default: {
        // approval.request carries the run_id the approve control must target.
        if (type === "approval.request" && typeof msg.run_id === "string") {
          approvalRunIdRef.current = msg.run_id;
        }
        // Everything else — tool.call, tool.result, websearch.result, and all
        // errand events — folds through the shared reducer into the same audit
        // timeline the chat thread renders.
        const { type: _t, ...rest } = msg;
        void _t;
        const frame: RawFrame = { event: type, data: rest };
        setState((s) => {
          const opened = s.connection === "open" ? s : { ...s, connection: "open" as const };
          return applyFrame(opened, frame);
        });
      }
    }
  }, [flushPlayback]);

  // ── teardown ────────────────────────────────────────────────────────────────
  const teardown = useCallback(() => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    try {
      processorRef.current?.disconnect();
    } catch {
      /* noop */
    }
    processorRef.current = null;
    try {
      analyserRef.current?.disconnect();
    } catch {
      /* noop */
    }
    analyserRef.current = null;
    try {
      sourceRef.current?.disconnect();
    } catch {
      /* noop */
    }
    sourceRef.current = null;
    try {
      micSinkRef.current?.disconnect();
    } catch {
      /* noop */
    }
    micSinkRef.current = null;
    flushPlayback();
    try {
      playGainRef.current?.disconnect();
    } catch {
      /* noop */
    }
    playGainRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
      void audioCtxRef.current.close();
    }
    audioCtxRef.current = null;
    playCursorRef.current = 0;
    bandRef.current = new Float32Array(BANDS);
    setLevel(0);
    setInterim("");
    setVoicePhase("idle");
    setActive(false);
  }, [flushPlayback]);

  const stop = useCallback(() => {
    sessionIdRef.current += 1;
    stoppingRef.current = true;
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "stop" }));
      } catch {
        /* noop */
      }
    }
    if (ws) {
      try {
        ws.close();
      } catch {
        /* noop */
      }
    }
    wsRef.current = null;
    teardown();
  }, [teardown]);

  // ── start: open the WS + mic, wire capture/playback/orb ─────────────────────
  const start = useCallback(
    async (model: string, profile: string) => {
      if (!supported) {
        setError("Voice isn't available in this browser.");
        return;
      }

      // Replace any existing or still-starting session before requesting a new
      // microphone. This makes Retry idempotent and guarantees one mic, one
      // AudioContext, and one relay WebSocket at a time.
      const sessionId = sessionIdRef.current + 1;
      sessionIdRef.current = sessionId;
      stoppingRef.current = true;
      const previousWs = wsRef.current;
      wsRef.current = null;
      if (previousWs) {
        try {
          previousWs.close();
        } catch {
          /* noop */
        }
      }
      teardown();
      setError(null);
      stoppingRef.current = false;
      // Fresh conversation state each session.
      setState({ ...initialRunState, connection: "connecting" });

      // Mint the one-shot relay ticket BEFORE asking for the microphone: a
      // signed-out user should be told so instead of being made to grant mic
      // permission for a socket that will be refused anyway. The ticket lives
      // 60s, which covers even a slow permission prompt.
      //
      // This await is the second place a second start() can interleave (the mic
      // prompt is the first), so the same sessionId guard brackets it: a stale
      // mint result is discarded rather than opening a second socket.
      let ticket: string;
      try {
        const res = await fetch(api("/api/voice/ticket"), {
          method: "POST",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) throw new Error(`ticket ${res.status}`);
        const body = (await res.json()) as { ticket?: unknown };
        if (typeof body.ticket !== "string" || !body.ticket) {
          throw new Error("ticket missing from response");
        }
        ticket = body.ticket;
      } catch {
        if (sessionId !== sessionIdRef.current) return;
        setError(SIGNED_OUT_MESSAGE);
        return;
      }
      if (sessionId !== sessionIdRef.current) return;

      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
          },
        });
      } catch (err) {
        if (sessionId !== sessionIdRef.current) return;
        setError(
          (err as Error).name === "NotAllowedError"
            ? "Microphone permission denied."
            : `Could not start microphone: ${(err as Error).message}`,
        );
        return;
      }
      if (sessionId !== sessionIdRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;

      // One AudioContext at the mic rate; the 16 kHz playback buffers are
      // resampled by Web Audio on playback.
      const AudioCtx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext })
          .webkitAudioContext;
      let audioCtx: AudioContext;
      try {
        audioCtx = new AudioCtx({ sampleRate: MIC_SAMPLE_RATE });
      } catch {
        // Some browsers reject an explicit rate; fall back to default.
        audioCtx = new AudioCtx();
      }
      audioCtxRef.current = audioCtx;
      // Resume within the user gesture (the orb tap) to satisfy autoplay policy.
      try {
        await audioCtx.resume();
      } catch {
        /* noop */
      }
      if (sessionId !== sessionIdRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        if (audioCtx.state !== "closed") void audioCtx.close();
        if (streamRef.current === stream) streamRef.current = null;
        if (audioCtxRef.current === audioCtx) audioCtxRef.current = null;
        return;
      }

      const source = audioCtx.createMediaStreamSource(stream);
      sourceRef.current = source;

      // Analyser → orb amplitude (real mic).
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.7;
      source.connect(analyser);
      analyserRef.current = analyser;

      // ScriptProcessor → int16 PCM frames sent to the backend. It must be
      // connected to a destination to pull audio, so route through a silent gain
      // (gain 0) — nothing of the mic is echoed to the speakers.
      const processor = audioCtx.createScriptProcessor(4096, 1, 1);
      const micSink = audioCtx.createGain();
      micSink.gain.value = 0;
      processor.onaudioprocess = (e) => {
        const ws = wsRef.current;
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        const input = e.inputBuffer.getChannelData(0);
        const pcm = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
          const s = Math.max(-1, Math.min(1, input[i]));
          pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        try {
          ws.send(pcm.buffer);
        } catch {
          /* transient send failure — the next frame will retry */
        }
      };
      source.connect(processor);
      processor.connect(micSink);
      micSink.connect(audioCtx.destination);
      processorRef.current = processor;
      micSinkRef.current = micSink;

      // Playback gain → speakers.
      const playGain = audioCtx.createGain();
      playGain.gain.value = 1;
      playGain.connect(audioCtx.destination);
      playGainRef.current = playGain;
      playCursorRef.current = audioCtx.currentTime;

      // Open the relay WS.
      const url = `${wsApi(
        `/api/voice/ws?model=${encodeURIComponent(model)}&profile=${encodeURIComponent(profile)}`,
      )}&ticket=${encodeURIComponent(ticket)}`;
      const ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        if (sessionId !== sessionIdRef.current || wsRef.current !== ws) {
          ws.close();
          return;
        }
        setActive(true);
        setVoicePhase("listening");
        setState((s) => ({ ...s, connection: "open" }));
        try {
          ws.send(JSON.stringify({ type: "start" }));
        } catch {
          /* noop */
        }
        rafRef.current = requestAnimationFrame(tick);
      };
      ws.onmessage = (ev) => {
        if (sessionId !== sessionIdRef.current || wsRef.current !== ws) return;
        if (typeof ev.data === "string") {
          try {
            handleEvent(JSON.parse(ev.data));
          } catch {
            /* ignore malformed frame */
          }
        } else if (ev.data instanceof ArrayBuffer) {
          playPcm(ev.data);
        }
      };
      ws.onerror = () => {
        if (sessionId !== sessionIdRef.current || wsRef.current !== ws) return;
        if (!stoppingRef.current) {
          setError("Voice connection error.");
        }
      };
      ws.onclose = (ev?: CloseEvent) => {
        if (sessionId !== sessionIdRef.current || wsRef.current !== ws) return;
        if (ev?.code === WS_UNAUTHORIZED) {
          // The relay refused the ticket (stale, replayed, or the session
          // expired between mint and handshake). Say what to do about it rather
          // than reporting a connection drop the user cannot act on.
          setError(SIGNED_OUT_MESSAGE);
        } else if (!stoppingRef.current) {
          // Dropped mid-session — keep the transcript/cards, flag the drop.
          setState((s) =>
            pushAuditEntry(
              s,
              new Date().toISOString(),
              "stream.connection_lost",
              "The voice connection dropped. Everything so far is preserved.",
              {},
            ),
          );
          setState((s) => ({ ...s, connection: "lost" }));
        }
        wsRef.current = null;
        teardown();
      };
    },
    [supported, token, tick, handleEvent, playPcm, teardown],
  );

  // Resolve the approval gate over the voice WS control channel.
  //
  // The deliverability check comes FIRST, before any state is mutated. It used
  // to run after: the card was moved to "approving" and only then did we look at
  // the socket, so a dropped relay left the UI claiming the spend was being
  // authorised while the verdict had gone nowhere. A verdict that evaporates is
  // the worst possible outcome here — the operator believes they approved, the
  // run is still sitting on the gate, and it will time out with no explanation.
  // Say plainly that it did not send, and leave the gate where it is so it can
  // be answered again. (Same principle as the HTTP paths: the difference between
  // "expired code", "binding limit reached", "card verification failed" and
  // "device not supported" is the difference between a ten-second recovery and a
  // support ticket — so never collapse any of them into silence.)
  const resolveApproval = useCallback((verdict: ApprovalResult) => {
    const runId = approvalRunIdRef.current;
    const ws = wsRef.current;

    if (!ws || ws.readyState !== WebSocket.OPEN || !runId) {
      setError(
        !runId
          ? "There is no approval waiting on this run — it may have already timed out."
          : "The voice connection is closed, so the approval was not sent. Reconnect and approve again.",
      );
      return;
    }

    try {
      ws.send(
        JSON.stringify({
          type: "approve",
          run_id: runId,
          approved: verdict.approved,
          ...(verdict.reason ? { reason: verdict.reason } : {}),
        }),
      );
    } catch {
      setError("Approval failed to reach the voice relay. Try again.");
      setState((s) => ({
        ...s,
        phase: "error",
        errorMessage: "Approval failed to reach the voice relay.",
      }));
      return;
    }

    // Only once the verdict is genuinely on the wire does the card move.
    setState((s) => ({
      ...s,
      approvalResult: verdict,
      phase: verdict.approved ? "approving" : s.phase,
    }));
  }, []);

  useEffect(() => () => stop(), [stop]);

  return {
    state,
    active,
    voicePhase,
    level,
    band: bandRef.current,
    interim,
    supported,
    error,
    start,
    stop,
    resolveApproval,
  };
}
