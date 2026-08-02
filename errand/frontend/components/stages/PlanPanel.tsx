"use client";

import { useState } from "react";
import type { PurchaseContext } from "@/lib/types";
import { money, tidySnippet } from "@/lib/format";
import s from "./stages.module.css";

function Tick() {
  return (
    <svg className={s.ruleTick} width="15" height="15" viewBox="0 0 16 16" fill="none">
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
    <svg className={s.chipDoc} width="13" height="13" viewBox="0 0 16 16" fill="none">
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
    <div className={s.card}>
      <div className={s.head}>
        <span className={s.stepNo}>01 · GROUND</span>
        <h2 className={s.title}>The policy behind the spend</h2>
        <span className={s.sub}>
          Budget cap
          <br />
          <strong style={{ color: "var(--brass)", fontSize: 15 }}>
            {money(context.budget_cents)}
          </strong>
        </span>
      </div>

      {merchant && (
        <div className={s.merchant}>
          Approved merchant:
          <span className={s.merchantName}>{merchant.name}</span>
        </div>
      )}

      <ul className={s.rules}>
        {context.rules.map((r, i) => (
          <li key={i} className={s.rule}>
            <Tick />
            {r}
          </li>
        ))}
      </ul>

      {context.citations.length > 0 && (
        <>
          <div className={s.sectionLabel}>Grounded in — Senso sources</div>
          <div className={s.chips}>
            {context.citations.map((c, i) => (
              <button
                key={i}
                className={s.chip}
                onClick={() => setOpenChip((o) => (o === i ? null : i))}
                aria-expanded={openChip === i}
              >
                <span className={s.chipTop}>
                  <DocGlyph />
                  {c.source}
                </span>
                {openChip === i && (
                  <span className={s.chipSnippet}>{tidySnippet(c.snippet)}</span>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
