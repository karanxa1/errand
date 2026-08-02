"use client";

/* ToolCard — the reusable animated tool-call card, the "visual MCP" unit.

   Anatomy (per chat-ui-brief.md):
   - Header row: bespoke tool icon + title (+ optional meta) + a status chip
     (running = shimmer text; done = check; error = alert). Click the header to
     collapse/expand the body.
   - Body: the tool's data, revealing with a height/opacity motion.

   Anti-slop / robustness:
   - Content is VISIBLE BY DEFAULT. The collapse is a pure-CSS grid-rows
     transition (1fr <-> 0fr), so the body's existence never depends on a JS
     reveal firing. Reduced-motion collapses the timing to ~0.
   - The card's ARRIVAL is a fade + slight-rise handled by `motion` on the
     wrapper, but its `initial` is disabled under reduced-motion and it always
     animates TO the visible state (never stranded hidden).
   - Elevation from tone + a self-colored top lip — no all-around drop shadow,
     no icon-in-a-tile (the mark sits bare, tinted by tone). */

import { useReducedMotion, motion } from "motion/react";
import { useState, type ReactNode } from "react";
import { AlertIcon, CheckIcon } from "./ToolIcons";
import s from "./ToolCard.module.css";

export type CardTone = "ok" | "warn" | "error";
export type CardStatus = "running" | "done" | "error";

export default function ToolCard({
  icon,
  title,
  meta,
  status = "done",
  tone = "ok",
  children,
  defaultOpen = true,
}: {
  icon: ReactNode;
  title: string;
  meta?: ReactNode;
  status?: CardStatus;
  tone?: CardTone;
  children?: ReactNode;
  defaultOpen?: boolean;
}) {
  const reduce = useReducedMotion();
  const [open, setOpen] = useState(defaultOpen);
  const collapsible = Boolean(children);

  const toneClass =
    tone === "error" ? s.error : tone === "warn" ? s.warn : "";

  return (
    <motion.div
      className={`${s.card} ${toneClass}`}
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.19, ease: [0.22, 0.8, 0.28, 1] }}
    >
      <button
        type="button"
        className={s.header}
        onClick={() => collapsible && setOpen((o) => !o)}
        aria-expanded={collapsible ? open : undefined}
        style={{ cursor: collapsible ? "pointer" : "default" }}
      >
        <span className={s.icon}>{icon}</span>
        <span className={s.titleWrap}>
          <span className={s.title}>{title}</span>
          {meta && <span className={s.meta}>{meta}</span>}
        </span>

        <StatusChip status={status} />

        {collapsible && (
          <svg
            className={`${s.chev} ${open ? s.chevOpen : ""}`}
            width="14"
            height="14"
            viewBox="0 0 16 16"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M4 6l4 4 4-4"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </button>

      {collapsible && (
        <div
          className={`${s.bodyGrid} ${open ? s.bodyOpen : ""}`}
          // Body is present in the DOM regardless; grid-rows animates the reveal.
        >
          <div className={s.bodyInner}>
            <div className={s.body}>{children}</div>
          </div>
        </div>
      )}
    </motion.div>
  );
}

function StatusChip({ status }: { status: CardStatus }) {
  if (status === "running") {
    return (
      <span className={`${s.chip} ${s.chipRunning}`}>
        <span className={s.shimmer}>Working</span>
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className={`${s.chip} ${s.chipError}`}>
        <AlertIcon size={13} />
      </span>
    );
  }
  return (
    <span className={`${s.chip} ${s.chipDone}`}>
      <CheckIcon size={13} />
    </span>
  );
}
