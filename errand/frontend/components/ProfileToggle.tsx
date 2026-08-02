"use client";

import { useLayoutEffect, useRef, useState } from "react";
import type { ProfileKind } from "@/lib/types";
import styles from "./ProfileToggle.module.css";

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
      className={styles.toggle}
      role="tablist"
      aria-label="Profile"
    >
      <span
        className={styles.indicator}
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
            className={`${styles.opt} ${active ? styles.optActive : ""}`}
            onClick={() => !disabled && onChange(o.key)}
          >
            <span className={styles.dot} />
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
