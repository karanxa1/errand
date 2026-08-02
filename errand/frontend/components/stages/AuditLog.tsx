"use client";

import { useState } from "react";
import type { AuditEntry } from "@/lib/types";
import { clock } from "@/lib/format";

/* AuditLog — renders EVERY stream event in arrival order with its timestamp,
   step name, and human detail. Each row expands to show the raw payload, so the
   full context→cart→approve→pay→email trail is auditable end to end. */

export default function AuditLog({ entries }: { entries: AuditEntry[] }) {
  const [open, setOpen] = useState<Set<number>>(new Set());

  const toggle = (id: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="bg-ink-050 rounded-card shadow-[inset_0_0_0_1px_var(--color-edge)] overflow-hidden">
      <div className="flex items-center gap-2.5 px-[18px] py-[14px] border-b border-edge">
        <LedgerGlyph />
        <span className="text-[12px] tracking-[0.12em] uppercase text-mid font-semibold">
          Audit log
        </span>
        <span className="ml-auto font-mono text-[11px] text-low">
          {entries.length} events
        </span>
      </div>

      {entries.length === 0 ? (
        <div className="px-[18px] py-[22px] text-low text-[13px]">
          Events will appear here the instant they stream in.
        </div>
      ) : (
        <ul className="list-none m-0 px-0 py-1.5 max-h-[420px] overflow-y-auto">
          {entries.map((e) => {
            const isOpen = open.has(e.id);
            const hasPayload = e.payload && Object.keys(e.payload).length > 0;
            return (
              <li key={e.id} className="px-[18px]">
                <button
                  className="grid grid-cols-[92px_150px_1fr_auto] gap-3 items-baseline w-full text-left bg-transparent border-none text-body py-[7px] border-b border-b-[rgba(160,240,200,0.045)] hover:text-hi"
                  onClick={() => hasPayload && toggle(e.id)}
                  aria-expanded={isOpen}
                  style={{ cursor: hasPayload ? "pointer" : "default" }}
                >
                  <span className="font-mono text-[11px] text-low">
                    {clock(e.at)}
                  </span>
                  <span className="font-mono text-[11.5px] text-green-soft whitespace-nowrap overflow-hidden text-ellipsis">
                    {e.step}
                  </span>
                  <span className="text-[12.5px] text-mid leading-[1.4]">
                    {e.detail}
                  </span>
                  {hasPayload ? (
                    <svg
                      className={`text-low transition-transform duration-[180ms] ease-[ease] justify-self-end ${
                        isOpen ? "rotate-90" : ""
                      }`}
                      width="12"
                      height="12"
                      viewBox="0 0 12 12"
                      fill="none"
                    >
                      <path
                        d="M4.5 3l3 3-3 3"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  ) : (
                    <span />
                  )}
                </button>
                {isOpen && hasPayload && (
                  <pre className="mt-1 mb-2.5 mx-0 px-[14px] py-3 bg-ink-000 rounded-[10px] shadow-[inset_0_0_0_1px_var(--color-edge)] font-mono text-[11px] leading-[1.5] text-mid overflow-x-auto whitespace-pre-wrap [word-break:break-word]">
                    {JSON.stringify(e.payload, null, 2)}
                  </pre>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function LedgerGlyph() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 16 16"
      fill="none"
      className="text-green"
      aria-hidden="true"
    >
      <rect
        x="3"
        y="2.5"
        width="10"
        height="11"
        rx="1.6"
        stroke="currentColor"
        strokeWidth="1.4"
      />
      <path
        d="M5.5 6h5M5.5 8.5h5M5.5 11h3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}
