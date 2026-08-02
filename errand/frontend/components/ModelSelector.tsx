"use client";

import { useEffect, useRef, useState } from "react";
import type { ModelOption } from "@/lib/types";
import { markFor } from "./Marks";

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
    <div className="relative" ref={wrapRef}>
      <button
        className="inline-flex items-center gap-2.5 bg-ink-100 text-body border-0 shadow-[inset_0_0_0_1px_var(--color-edge)] rounded-full px-3.5 py-1.5 transition-[box-shadow,background-color] duration-200 ease-[ease] [&:hover:not(:disabled)]:bg-ink-150 [&:hover:not(:disabled)]:shadow-[inset_0_0_0_1px_var(--color-edge-strong)] disabled:opacity-[0.55] disabled:cursor-default"
        onClick={() => !disabled && setOpen((o) => !o)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {/* Bare mark, no tile behind it — the drawn mark carries its own weight. */}
        <span className="text-green inline-flex w-6 h-6 items-center justify-center">
          {markFor(current.key, 18)}
        </span>
        <span className="flex flex-col items-start leading-[1.15]">
          <span className="text-[13.5px] font-semibold text-hi">
            {current.label}
          </span>
          <span className="text-[10.5px] text-low">{current.tagline}</span>
        </span>
        <svg
          className={`text-low ml-0.5 transition-transform duration-200 ease-[ease] ${
            open ? "rotate-180" : ""
          }`}
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
        <div
          className="absolute top-[calc(100%+8px)] right-0 min-w-[244px] bg-ink-100 rounded-card shadow-[inset_0_0_0_1px_var(--color-edge),0_12px_30px_-12px_rgba(0,0,0,0.7)] p-1.5 z-30"
          role="listbox"
        >
          {models.map((m) => {
            const active = m.key === value;
            return (
              <button
                key={m.key}
                role="option"
                aria-selected={active}
                className={`flex items-center gap-[11px] w-full text-left border-0 text-body px-2.5 py-[9px] rounded-[10px] transition-[background-color] duration-[160ms] ease-[ease] hover:bg-ink-200 ${
                  active ? "bg-ink-150" : "bg-transparent"
                }`}
                onClick={() => {
                  onChange(m.key);
                  setOpen(false);
                }}
              >
                <span className="text-green flex-none w-7 h-7 inline-flex items-center justify-center">
                  {markFor(m.key, 22)}
                </span>
                <span className="flex flex-col leading-[1.2]">
                  <span className="text-[13.5px] font-semibold text-hi">
                    {m.label}
                  </span>
                  <span className="text-[11px] text-mid">{m.tagline}</span>
                </span>
                <svg
                  className={`ml-auto text-green ${active ? "opacity-100" : "opacity-0"}`}
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
