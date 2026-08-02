"use client";

import { useState } from "react";
import type { PurchaseContext } from "@/lib/types";
import { money, tidySnippet } from "@/lib/format";

/* Shared stage-card language. Elevation from tone + a self-coloured top lip —
   no drawn contrasting border, no all-around shadow. */
const CARD =
  "bg-[image:linear-gradient(180deg,var(--color-ink-100),var(--color-ink-050))] rounded-card shadow-[inset_0_1px_0_rgba(160,240,200,0.06),inset_0_0_0_1px_var(--color-edge)] px-[22px] py-5";
const HEAD = "flex items-center gap-3 mb-4";
const STEP_NO =
  "font-mono text-[11px] text-green tracking-[0.06em] px-2 py-[3px] rounded-md bg-ink-200 shadow-[inset_0_0_0_1px_var(--color-edge)] flex-none";
const TITLE =
  "font-display text-[22px] text-hi m-0 tracking-[0.01em] leading-[1.1]";
const SUB = "text-mid text-[13px] ml-auto text-right";

function Tick() {
  return (
    <svg
      className="flex-none mt-0.5 text-green"
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
  );
}

function DocGlyph() {
  return (
    <svg
      className="text-green flex-none"
      width="13"
      height="13"
      viewBox="0 0 16 16"
      fill="none"
    >
      <path
        d="M4 2.5h5l3 3V13a.5.5 0 0 1-.5.5h-7A.5.5 0 0 1 4 13V3a.5.5 0 0 1 .5-.5Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M9 2.5V6h3" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  );
}

export default function PlanPanel({ context }: { context: PurchaseContext }) {
  const [openChip, setOpenChip] = useState<number | null>(0);
  const merchant = context.approved_merchants[0];

  return (
    <div className={CARD}>
      <div className={HEAD}>
        <span className={STEP_NO}>01 · GROUND</span>
        <h2 className={TITLE}>The policy behind the spend</h2>
        <span className={SUB}>
          Budget cap
          <br />
          <strong className="text-brass text-[15px]">
            {money(context.budget_cents)}
          </strong>
        </span>
      </div>

      {merchant && (
        <div className="inline-flex items-center gap-2 text-[13px] text-mid mb-[14px]">
          Approved merchant:
          <span className="text-hi font-semibold">{merchant.name}</span>
        </div>
      )}

      <ul className="list-none m-0 p-0 grid gap-2">
        {context.rules.map((r, i) => (
          <li
            key={i}
            className="flex gap-2.5 items-start text-[13.5px] text-body leading-[1.4]"
          >
            <Tick />
            {r}
          </li>
        ))}
      </ul>

      {context.citations.length > 0 && (
        <>
          <div className="text-[11px] tracking-[0.12em] uppercase text-low mt-[18px] mb-2.5 font-semibold">
            Grounded in — Senso sources
          </div>
          <div className="flex flex-wrap gap-2">
            {context.citations.map((c, i) => (
              <button
                key={i}
                className="inline-flex flex-col gap-[3px] max-w-full text-left border-none bg-ink-150 text-body rounded-[10px] px-[11px] py-2 shadow-[inset_0_0_0_1px_var(--color-edge)] transition-[background] duration-[180ms] ease-[ease] hover:bg-ink-200"
                onClick={() => setOpenChip((o) => (o === i ? null : i))}
                aria-expanded={openChip === i}
              >
                <span className="inline-flex items-center gap-[7px] text-[12px] text-hi font-semibold">
                  <DocGlyph />
                  {c.source}
                </span>
                {openChip === i && (
                  <span className="text-[11.5px] text-mid leading-[1.4] max-w-[340px]">
                    {tidySnippet(c.snippet)}
                  </span>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
