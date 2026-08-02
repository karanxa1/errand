"use client";

import { useEffect, useRef, useState } from "react";
import type { ModelOption } from "@/lib/types";
import { markFor } from "./Marks";
import styles from "./ModelSelector.module.css";

export default function ModelSelector({
  models,
  value,
  onChange,
  disabled,
}: {
  models: ModelOption[];
  value: string;
  onChange: (key: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const current = models.find((m) => m.key === value) ?? models[0];

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!current) return null;

  return (
    <div className={styles.wrap} ref={wrapRef}>
      <button
        className={styles.trigger}
        onClick={() => !disabled && setOpen((o) => !o)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className={styles.mark}>{markFor(current.key, 18)}</span>
        <span className={styles.labels}>
          <span className={styles.name}>{current.label}</span>
          <span className={styles.tag}>{current.tagline}</span>
        </span>
        <svg
          className={`${styles.chev} ${open ? styles.chevOpen : ""}`}
          width="14"
          height="14"
          viewBox="0 0 16 16"
          fill="none"
        >
          <path
            d="M4 6l4 4 4-4"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {open && (
        <div className={styles.menu} role="listbox">
          {models.map((m) => {
            const active = m.key === value;
            return (
              <button
                key={m.key}
                role="option"
                aria-selected={active}
                className={`${styles.item} ${active ? styles.itemActive : ""}`}
                onClick={() => {
                  onChange(m.key);
                  setOpen(false);
                }}
              >
                <span className={styles.itemMark}>{markFor(m.key, 22)}</span>
                <span className={styles.itemBody}>
                  <span className={styles.itemName}>{m.label}</span>
                  <span className={styles.itemTag}>{m.tagline}</span>
                </span>
                <svg
                  className={styles.check}
                  width="15"
                  height="15"
                  viewBox="0 0 16 16"
                  fill="none"
                >
                  <path
                    d="M3 8.5l3 3 7-7"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
