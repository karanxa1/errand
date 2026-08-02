"use client";

/* StatusLine — a quiet, inline status row for events that don't warrant a full
   card (inbox ready, approval granted, reported to Prava, aborted). A small
   tinted mark + one line; no box behind the icon. */

import { useReducedMotion, motion } from "motion/react";
import type { ReactNode } from "react";

// The tone only ever re-tints the mark; the line itself stays quiet.
const MARK_TONE = {
  ok: "text-green",
  warn: "text-brass",
  muted: "text-low",
} as const;

export default function StatusLine({
  icon,
  text,
  tone = "muted",
}: {
  icon: ReactNode;
  text: string;
  tone?: "ok" | "warn" | "muted";
}) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="flex items-center gap-[9px] px-1 py-[3px] text-mid"
      // Entrance animates POSITION ONLY, never opacity. If the animation never
      // runs — a hydration hiccup, a backgrounded tab, a throttled engine, a
      // screenshot pass — an element that started at opacity 0 is simply gone,
      // and here that would mean a tool card, a status line, or the approval
      // control that gates real spend rendering as an empty void. Resting at
      // full opacity means the worst case is a card that appears without sliding.
      initial={reduce ? false : { y: 5 }}
      animate={{ y: 0 }}
      transition={{ duration: 0.17, ease: [0.22, 0.8, 0.28, 1] }}
    >
      <span
        className={`inline-flex flex-none items-center justify-center ${MARK_TONE[tone]}`}
      >
        {icon}
      </span>
      <span className="min-w-0 overflow-hidden text-ellipsis text-[12.5px] leading-[1.4]">
        {text}
      </span>
    </motion.div>
  );
}
