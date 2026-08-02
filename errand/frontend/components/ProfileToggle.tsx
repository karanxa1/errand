"use client";

import { useLayoutEffect, useRef, useState } from "react";
import type { ProfileKind } from "@/lib/types";

const OPTS: { key: ProfileKind; label: string }[] = [
  { key: "business", label: "Business" },
  { key: "personal", label: "Personal" },
];

export default function ProfileToggle({
  value,
  onChange,
  disabled,
}: {
  value: ProfileKind;
  onChange: (v: ProfileKind) => void;
  disabled?: boolean;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const [ind, setInd] = useState({ left: 0, width: 0 });

  useLayoutEffect(() => {
    const i = OPTS.findIndex((o) => o.key === value);
    const el = refs.current[i];
    if (el) setInd({ left: el.offsetLeft, width: el.offsetWidth });
  }, [value]);

  return (
    <div
      className="inline-flex relative p-[3px] bg-ink-100 rounded-full shadow-[inset_0_0_0_1px_var(--color-edge)] gap-0"
      role="tablist"
      aria-label="Profile"
    >
      <span
        className="absolute top-[3px] bottom-[3px] rounded-full bg-[linear-gradient(180deg,var(--color-ink-250),var(--color-ink-200))] shadow-[inset_0_1px_0_rgba(160,240,200,0.14),0_1px_2px_rgba(0,0,0,0.35)] transition-transform duration-[320ms] ease-[cubic-bezier(0.22,0.8,0.28,1)] z-0"
        style={{ transform: `translateX(${ind.left - 3}px)`, width: ind.width }}
      />
      {OPTS.map((o, i) => {
        const active = o.key === value;
        return (
          <button
            key={o.key}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            aria-selected={active}
            disabled={disabled}
            className={`relative z-[1] border-0 bg-transparent text-[12.5px] tracking-[0.02em] [font-weight:550] px-4 py-2 rounded-full transition-[color] duration-[240ms] ease-[ease] inline-flex items-center gap-[7px] whitespace-nowrap disabled:cursor-default ${
              active ? "text-hi" : "text-low"
            }`}
            onClick={() => !disabled && onChange(o.key)}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                active ? "bg-green opacity-100" : "bg-current opacity-[0.55]"
              }`}
            />
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
