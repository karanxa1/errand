"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import "./controls.anim.css";

// The dispatch arrow slides up-right when its button is hovered (and not
// disabled). Shared by the labelled send, the round send and the auth submit.
const ARROW =
  "transition-transform duration-200 ease-[ease] " +
  "[button:hover:not(:disabled)_&]:translate-x-0.5 " +
  "[button:hover:not(:disabled)_&]:-translate-y-0.5";

export default function Composer({
  value,
  onChange,
  onSubmit,
  disabled,
  listening,
  onToggleMic,
  micSupported,
  micDisabled,
  micSlot,
  hint,
  error,
  sendLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
  listening: boolean;
  onToggleMic: () => void;
  micSupported: boolean;
  // Independently disable ONLY the mic control. The orb must stay tappable to
  // STOP a live voice session even while the textarea + send are locked.
  micDisabled?: boolean;
  // Optional custom mic control (the VoiceOrb). When given, it replaces the
  // default round mic button so the signature orb IS the tap-to-talk control.
  micSlot?: ReactNode;
  hint?: string;
  error?: string | null;
  // When set, the send button shows a label + arrow (empty-state hero). When
  // omitted, send is a compact circular icon action (in-thread composer).
  sendLabel?: string;
}) {
  const [focus, setFocus] = useState(false);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  // Auto-grow the textarea to fit content.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
  }, [value]);

  // Voice-first: the primary action stays available even with an empty field
  // (the page falls back to the profile's preset intent). It only locks while a
  // run is in flight.
  const canSend = !disabled;

  return (
    <div className="w-full">
      <div
        className={`flex items-end gap-3 bg-ink-100 rounded-panel py-3 pr-3 pl-[18px] transition-shadow duration-200 ease-[ease] ${
          focus
            ? "shadow-[inset_0_0_0_1px_var(--color-edge-strong),0_0_0_3px_var(--color-green-glow)]"
            : "shadow-[inset_0_0_0_1px_var(--color-edge)]"
        }`}
      >
        <div className="flex-1 flex flex-col gap-0.5 min-w-0">
          <textarea
            ref={taRef}
            className="w-full resize-none border-0 bg-transparent text-hi font-body text-[16px] leading-[1.45] py-1.5 max-h-40 placeholder:text-low focus:outline-none"
            rows={1}
            placeholder="Speak your errand, or type it — “Restock the office pantry, under $200.”"
            value={value}
            disabled={disabled}
            onFocus={() => setFocus(true)}
            onBlur={() => setFocus(false)}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (canSend) onSubmit();
              }
            }}
          />
          <span
            className={`text-[11px] min-h-[14px] ${error ? "text-danger" : "text-low"}`}
          >
            {error || hint || ""}
          </span>
        </div>

        <div className="flex items-center gap-2.5 flex-none">
          {micSupported &&
            (micSlot ? (
              <button
                type="button"
                className="flex-none w-[46px] h-[46px] border-0 bg-transparent p-0 rounded-full inline-flex items-center justify-center leading-[0] disabled:cursor-default"
                onClick={onToggleMic}
                disabled={micDisabled}
                aria-pressed={listening}
                aria-label={listening ? "Stop listening" : "Speak your errand"}
                title={listening ? "Stop listening" : "Speak your errand"}
              >
                {micSlot}
              </button>
            ) : (
              /* Mic — a soft round control; when live it pulses via a
                 self-colored ring, no blurred halo. */
              <button
                type="button"
                className={`relative w-[46px] h-[46px] rounded-full border-0 inline-flex items-center justify-center transition-[background-color,color] duration-200 ease-[ease] [&:hover:not(:disabled)]:bg-ink-250 [&:hover:not(:disabled)]:text-hi disabled:opacity-50 disabled:cursor-default ${
                  listening
                    ? "bg-green text-on-accent shadow-[inset_0_0_0_1px_rgba(255,255,255,0.2),0_0_0_4px_var(--color-green-glow)] after:content-[''] after:absolute after:-inset-1.5 after:rounded-full after:border-[1.5px] after:border-green after:opacity-50 after:animate-[micring_1.6s_ease-out_infinite]"
                    : "bg-ink-200 text-body shadow-[inset_0_0_0_1px_var(--color-edge)]"
                }`}
                onClick={onToggleMic}
                disabled={micDisabled}
                aria-pressed={listening}
                aria-label={listening ? "Stop listening" : "Speak your errand"}
                title={listening ? "Stop listening" : "Speak your errand"}
              >
                <MicGlyph />
              </button>
            ))}
          {sendLabel ? (
            /* Send — a solid, grounded action. Custom up-right dispatch arrow
               (not the stock horizontal one). No lift-on-hover; a clean tonal +
               icon shift. */
            <button
              type="button"
              className="h-[46px] pl-5 pr-[18px] rounded-full border-0 bg-green text-on-accent [font-weight:650] text-sm inline-flex items-center gap-[9px] shadow-[inset_0_1px_0_rgba(255,255,255,0.22)] transition-[background-color] duration-[180ms] ease-[ease] [&:hover:not(:disabled)]:bg-green-soft disabled:bg-ink-200 disabled:text-low disabled:shadow-[inset_0_0_0_1px_var(--color-edge)] disabled:cursor-default"
              onClick={() => canSend && onSubmit()}
              disabled={!canSend}
            >
              {sendLabel}
              <svg
                className={ARROW}
                width="16"
                height="16"
                viewBox="0 0 16 16"
                fill="none"
                aria-hidden="true"
              >
                <path
                  d="M4 12L12 4M12 4H6M12 4V10"
                  stroke="currentColor"
                  strokeWidth="1.9"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          ) : (
            /* Compact circular send — the in-thread action. */
            <button
              type="button"
              className="flex-none w-[46px] h-[46px] rounded-full border-0 bg-green text-on-accent inline-flex items-center justify-center shadow-[inset_0_1px_0_rgba(255,255,255,0.22)] transition-[background-color] duration-[180ms] ease-[ease] [&:hover:not(:disabled)]:bg-green-soft disabled:bg-ink-200 disabled:text-low disabled:shadow-[inset_0_0_0_1px_var(--color-edge)] disabled:cursor-default"
              onClick={() => canSend && onSubmit()}
              disabled={!canSend}
              aria-label="Run errand"
              title="Run errand"
            >
              <svg
                className={ARROW}
                width="17"
                height="17"
                viewBox="0 0 16 16"
                fill="none"
                aria-hidden="true"
              >
                <path
                  d="M4 12L12 4M12 4H6M12 4V10"
                  stroke="currentColor"
                  strokeWidth="1.9"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function MicGlyph() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect
        x="7"
        y="2.5"
        width="6"
        height="10"
        rx="3"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <path
        d="M4.5 9.5a5.5 5.5 0 0 0 11 0"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
      <path
        d="M10 15v2.5"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}
