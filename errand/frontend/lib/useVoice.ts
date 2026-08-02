// useVoice — the browser voice seam.
//
// IMPORTANT ARCHITECTURE NOTE (read before swapping in Deepgram):
// Deepgram browser token minting is not available on this key, so the browser
// does NOT connect to Deepgram directly here. Instead:
//   1. Mic amplitude is read locally via the Web Audio AnalyserNode and exposed
//      as `level` (0..1) + a frequency band array. This is what drives the
//      signature voice orb — it reacts to REAL microphone audio, not a fake.
//   2. Transcription uses the browser's Web Speech API (SpeechRecognition) and
//      streams interim/final text into the composer via `onTranscript`.
//
// FUTURE DEEPGRAM RELAY SEAM: to replace Web Speech transcription with Deepgram,
// keep this hook's public surface (start/stop, level, band, isListening,
// onTranscript) and swap ONLY the transcription source: pipe the same
// MediaStream into a MediaRecorder / AudioWorklet, ship PCM to a backend
// WebSocket that relays to wss://agent.deepgram.com, and feed the returned
// ConversationText transcripts to onTranscript. The orb (Web Audio path) stays
// untouched. Nothing else in the app depends on Web Speech.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// Minimal typings for the vendor-prefixed SpeechRecognition API.
interface SpeechRecognitionResultItem {
  transcript: string;
}
interface SpeechRecognitionResult {
  0: SpeechRecognitionResultItem;
  isFinal: boolean;
  length: number;
}
interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: {
    length: number;
    [i: number]: SpeechRecognitionResult;
  };
}
interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((e: SpeechRecognitionEventLike) => void) | null;
  onerror: ((e: { error?: string }) => void) | null;
  onend: (() => void) | null;
}

type RecognitionCtor = new () => SpeechRecognitionLike;

function getRecognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor;
    webkitSpeechRecognition?: RecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export interface VoiceHandlers {
  // Called with the live composite transcript (interim + final concatenated).
  onTranscript?: (text: string, isFinal: boolean) => void;
}

export interface VoiceApi {
  isListening: boolean;
  supported: boolean;
  speechSupported: boolean;
  error: string | null;
  // 0..1 smoothed overall amplitude.
  level: number;
  // Normalised frequency bands (low->high), 0..1 each; drives orb petals.
  band: Float32Array;
  start: () => Promise<void>;
  stop: () => void;
}

const BANDS = 5;

export function useVoice(handlers: VoiceHandlers = {}): VoiceApi {
  const [isListening, setIsListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState(0);
  const bandRef = useRef<Float32Array>(new Float32Array(BANDS));
  const [, force] = useState(0);

  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const finalTextRef = useRef("");
  const onTranscriptRef = useRef(handlers.onTranscript);
  onTranscriptRef.current = handlers.onTranscript;

  const speechSupported =
    typeof window !== "undefined" && getRecognitionCtor() !== null;
  const supported =
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia;

  const tick = useCallback(() => {
    const analyser = analyserRef.current;
    if (!analyser) return;
    const bins = analyser.frequencyBinCount;
    const freq = new Uint8Array(bins);
    analyser.getByteFrequencyData(freq);

    // Overall level from a perceptually useful slice (skip DC + very high).
    let sum = 0;
    const lo = 2;
    const hi = Math.min(bins, Math.floor(bins * 0.6));
    for (let i = lo; i < hi; i++) sum += freq[i];
    const avg = sum / (hi - lo) / 255;
    // Ease toward the new level for a live-but-not-jittery orb.
    setLevel((prev) => prev + (avg - prev) * 0.35);

    // Split into BANDS log-ish groups for the orb's independent lobes.
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

  const stop = useCallback(() => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
      void audioCtxRef.current.close();
    }
    audioCtxRef.current = null;
    analyserRef.current = null;
    bandRef.current = new Float32Array(BANDS);
    setLevel(0);
    setIsListening(false);
  }, []);

  const start = useCallback(async () => {
    if (!supported) {
      setError("Microphone access isn't available in this browser.");
      return;
    }
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const AudioCtx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext })
          .webkitAudioContext;
      const audioCtx = new AudioCtx();
      audioCtxRef.current = audioCtx;
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.7;
      source.connect(analyser);
      analyserRef.current = analyser;

      setIsListening(true);
      rafRef.current = requestAnimationFrame(tick);

      // Web Speech transcription (best-effort; orb works regardless).
      const Ctor = getRecognitionCtor();
      if (Ctor) {
        finalTextRef.current = "";
        const rec = new Ctor();
        rec.continuous = true;
        rec.interimResults = true;
        rec.lang = "en-US";
        rec.onresult = (e) => {
          let interim = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            const r = e.results[i];
            const text = r[0].transcript;
            if (r.isFinal) finalTextRef.current += text;
            else interim += text;
          }
          const composite = (finalTextRef.current + interim).trim();
          onTranscriptRef.current?.(composite, interim === "");
        };
        rec.onerror = (ev) => {
          // "no-speech"/"aborted" are benign; surface the rest quietly.
          if (ev.error && ev.error !== "no-speech" && ev.error !== "aborted") {
            setError(`Speech recognition: ${ev.error}`);
          }
        };
        rec.onend = () => {
          // If still listening (mic held), Chrome sometimes ends the session;
          // let the user's explicit stop drive teardown instead of auto-restart
          // loops, but keep the orb alive.
        };
        recognitionRef.current = rec;
        try {
          rec.start();
        } catch {
          /* already started */
        }
      }
    } catch (err) {
      setError(
        (err as Error).name === "NotAllowedError"
          ? "Microphone permission denied."
          : `Could not start microphone: ${(err as Error).message}`,
      );
      stop();
    }
  }, [supported, stop, tick]);

  useEffect(() => () => stop(), [stop]);

  return {
    isListening,
    supported,
    speechSupported,
    error,
    level,
    band: bandRef.current,
    start,
    stop,
  };
}
