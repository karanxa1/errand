"use client";

import { useState } from "react";
import type { AuditEntry } from "@/lib/types";
import { clock } from "@/lib/format";
import s from "./AuditLog.module.css";

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
    <div className={s.wrap}>
      <div className={s.header}>
        <LedgerGlyph />
        <span className={s.headTitle}>Audit log</span>
        <span className={s.count}>{entries.length} events</span>
      </div>

      {entries.length === 0 ? (
        <div className={s.empty}>
          Events will appear here the instant they stream in.
        </div>
      ) : (
        <ul className={s.list}>
          {entries.map((e) => {
            const isOpen = open.has(e.id);
            const hasPayload = e.payload && Object.keys(e.payload).length > 0;
            return (
              <li key={e.id} className={s.entry}>
                <button
                  className={s.line}
                  onClick={() => hasPayload && toggle(e.id)}
                  aria-expanded={isOpen}
                  style={{ cursor: hasPayload ? "pointer" : "default" }}
                >
                  <span className={s.time}>{clock(e.at)}</span>
                  <span className={s.step}>{e.step}</span>
                  <span className={s.detail}>{e.detail}</span>
                  {hasPayload ? (
                    <svg
                      className={`${s.caret} ${isOpen ? s.caretOpen : ""}`}
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
                  <pre className={s.payload}>
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
      style={{ color: "var(--green)" }}
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
