"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./Composer.module.css";

export default function Composer({
  value,
  onChange,
  onSubmit,
  disabled,
  listening,
  onToggleMic,
  micSupported,
  hint,
  error,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
  listening: boolean;
  onToggleMic: () => void;
  micSupported: boolean;
  hint?: string;
  error?: string | null;
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
    <div className={styles.composer}>
      <div className={`${styles.row} ${focus ? styles.rowFocus : ""}`}>
        <div className={styles.field}>
          <textarea
            ref={taRef}
            className={styles.textarea}
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
          <span className={`${styles.hint} ${error ? styles.err : ""}`}>
            {error || hint || ""}
          </span>
        </div>

        <div className={styles.actions}>
          {micSupported && (
            <button
              type="button"
              className={`${styles.mic} ${listening ? styles.micLive : ""}`}
              onClick={onToggleMic}
              disabled={disabled}
              aria-pressed={listening}
              aria-label={listening ? "Stop listening" : "Speak your errand"}
              title={listening ? "Stop listening" : "Speak your errand"}
            >
              <MicGlyph />
            </button>
          )}
          <button
            type="button"
            className={styles.send}
            onClick={() => canSend && onSubmit()}
            disabled={!canSend}
          >
            Run errand
            <svg
              className={styles.sendArrow}
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
