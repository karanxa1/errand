"use client";

/* StatusLine — a quiet, inline status row for events that don't warrant a full
   card (inbox ready, approval granted, reported to Prava, aborted). A small
   tinted mark + one line; no box behind the icon. */

import { useReducedMotion, motion } from "motion/react";
import type { ReactNode } from "react";
import s from "./StatusLine.module.css";

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
  const toneClass = tone === "ok" ? s.ok : tone === "warn" ? s.warn : "";
  return (
    <motion.div
      className={`${s.line} ${toneClass}`}
      initial={reduce ? false : { opacity: 0, y: 5 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.17, ease: [0.22, 0.8, 0.28, 1] }}
    >
      <span className={s.mark}>{icon}</span>
      <span className={s.text}>{text}</span>
    </motion.div>
  );
}
