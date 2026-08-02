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
import "./chat.anim.css";

export type CardTone = "ok" | "warn" | "error";
export type CardStatus = "running" | "done" | "error";

/* Card surface + collapsible body. Elevation from tone and a self-colored top
   lip; no all-around drop shadow, no drawn contrasting border, no icon tile.
   Warn / error tones shift the top lip + edge warmth, not a loud fill. */
const CARD_BASE =
  "overflow-hidden rounded-card bg-[linear-gradient(180deg,var(--color-ink-100),var(--color-ink-050))]";

const CARD_TONE: Record<CardTone, string> = {
  ok: "shadow-[inset_0_1px_0_rgba(160,240,200,0.06),inset_0_0_0_1px_var(--color-edge)]",
  warn: "shadow-[inset_0_1px_0_rgba(232,180,95,0.16),inset_0_0_0_1px_rgba(232,180,95,0.2)]",
  error:
    "shadow-[inset_0_1px_0_rgba(255,122,107,0.16),inset_0_0_0_1px_rgba(255,122,107,0.22)]",
};

/* Bare mark, tinted by tone — no tile behind it. */
const ICON_TONE: Record<CardTone, string> = {
  ok: "text-green",
  warn: "text-brass",
  error: "text-danger",
};

const BODY_GRID_BASE =
  "grid transition-[grid-template-rows] duration-200 ease-[ease] motion-reduce:transition-none";

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

  return (
    <motion.div
      className={`${CARD_BASE} ${CARD_TONE[tone]}`}
      // Entrance animates POSITION ONLY, never opacity. If the animation never
      // runs — a hydration hiccup, a backgrounded tab, a throttled engine, a
      // screenshot pass — an element that started at opacity 0 is simply gone,
      // and here that would mean a tool card, a status line, or the approval
      // control that gates real spend rendering as an empty void. Resting at
      // full opacity means the worst case is a card that appears without sliding.
      initial={reduce ? false : { y: 6 }}
      animate={{ y: 0 }}
      transition={{ duration: 0.19, ease: [0.22, 0.8, 0.28, 1] }}
    >
      <button
        type="button"
        className="flex w-full items-center gap-3 border-none bg-transparent px-4 py-[14px] text-left text-body"
        onClick={() => collapsible && setOpen((o) => !o)}
        aria-expanded={collapsible ? open : undefined}
        style={{ cursor: collapsible ? "pointer" : "default" }}
      >
        <span
          className={`inline-flex h-[22px] w-[22px] flex-none items-center justify-center ${ICON_TONE[tone]}`}
        >
          {icon}
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-px">
          <span className="text-[14px] font-semibold leading-[1.25] tracking-[0.01em] text-hi">
            {title}
          </span>
          {meta && (
            <span className="overflow-hidden text-ellipsis text-[12px] leading-[1.3] text-mid">
              {meta}
            </span>
          )}
        </span>

        <StatusChip status={status} />

        {collapsible && (
          <svg
            className={`flex-none text-low transition-transform duration-200 ease-[ease] ${
              open ? "rotate-180" : ""
            }`}
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
          className={`${BODY_GRID_BASE} ${
            open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
          }`}
          // Body is present in the DOM regardless; grid-rows animates the reveal.
        >
          <div className="min-h-0 overflow-hidden">
            <div className="px-4 pb-4">{children}</div>
          </div>
        </div>
      )}
    </motion.div>
  );
}

function StatusChip({ status }: { status: CardStatus }) {
  const chip =
    "inline-flex h-[22px] flex-none items-center justify-center gap-1.5 rounded-full";

  if (status === "running") {
    return (
      <span
        className={`${chip} px-[7px] bg-ink-200 font-mono text-[10.5px] uppercase tracking-[0.1em] shadow-[inset_0_0_0_1px_var(--color-edge)]`}
      >
        {/* Text shimmer — a light sweep across the label, gated by reduced-motion. */}
        <span className="animate-[sweep_1.6s_ease-in-out_infinite] bg-[linear-gradient(90deg,var(--color-low)_0%,var(--color-low)_35%,var(--color-green-soft)_50%,var(--color-low)_65%,var(--color-low)_100%)] [background-size:220%_100%] bg-clip-text text-transparent [-webkit-text-fill-color:transparent] motion-reduce:animate-none motion-reduce:bg-none motion-reduce:text-mid motion-reduce:[-webkit-text-fill-color:currentColor]">
          Working
        </span>
      </span>
    );
  }
  if (status === "error") {
    return (
      <span
        className={`${chip} w-[22px] p-0 bg-[rgba(255,122,107,0.12)] text-danger shadow-[inset_0_0_0_1px_rgba(255,122,107,0.28)]`}
      >
        <AlertIcon size={13} />
      </span>
    );
  }
  return (
    <span
      className={`${chip} w-[22px] p-0 bg-green text-on-accent shadow-[inset_0_1px_0_rgba(255,255,255,0.22)]`}
    >
      <CheckIcon size={13} />
    </span>
  );
}
