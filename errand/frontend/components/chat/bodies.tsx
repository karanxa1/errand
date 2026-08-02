"use client";

/* Tool-card bodies — the per-tool visual renderers that live INSIDE a ToolCard.
   Each reuses the shared stage stylesheet (budget meter, citation chips, item
   rows, progress rail) but WITHOUT the outer card/step-header, since ToolCard
   already supplies the frame + status. This is the CartPanel/PlanPanel content
   reused inside tool cards, as the brief asks. */

import { useState } from "react";
import type {
  CartResult,
  PurchaseContext,
  RunDone,
} from "@/lib/types";
import type { RunState } from "@/lib/useErrandRun";
import { money, tidySnippet } from "@/lib/format";
import s from "../stages/stages.module.css";
import c from "./bodies.module.css";

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

/* context.loaded — policy: budget headline, rules, Senso citation chips. */
export function PolicyBody({ context }: { context: PurchaseContext }) {
  const [openChip, setOpenChip] = useState<number | null>(0);
  const merchant = context.approved_merchants[0];

  return (
    <div className={c.stack}>
      <div className={c.figRow}>
        <div className={c.fig}>
          <span className={c.figKey}>Budget cap</span>
          <span className={c.figVal}>{money(context.budget_cents)}</span>
        </div>
        {merchant && (
          <div className={c.fig}>
            <span className={c.figKey}>Approved merchant</span>
            <span className={c.figValText}>{merchant.name}</span>
          </div>
        )}
      </div>

      {context.rules.length > 0 && (
        <ul className={s.rules}>
          {context.rules.map((r, i) => (
            <li key={i} className={s.rule}>
              <Tick />
              {r}
            </li>
          ))}
        </ul>
      )}

      {context.citations.length > 0 && (
        <>
          <div className={s.sectionLabel}>Grounded in — Senso sources</div>
          <div className={s.chips}>
            {context.citations.map((cit, i) => (
              <button
                key={i}
                className={s.chip}
                onClick={() => setOpenChip((o) => (o === i ? null : i))}
                aria-expanded={openChip === i}
                type="button"
              >
                <span className={s.chipTop}>
                  <DocGlyph />
                  {cit.source}
                </span>
                {openChip === i && (
                  <span className={s.chipSnippet}>{tidySnippet(cit.snippet)}</span>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/* cart.built — line items, total, budget meter. */
export function CartBody({
  cart,
  budgetCents,
}: {
  cart: CartResult;
  budgetCents?: number;
}) {
  const budget = budgetCents ?? 0;
  const pct = budget > 0 ? Math.min(1, cart.total_cents / budget) : 0;
  const over = budget > 0 && cart.total_cents > budget;

  return (
    <div className={c.stack}>
      <div className={s.items}>
        {cart.items.map((it, i) => (
          <div key={i} className={s.item}>
            <span className={s.itemName}>{it.name}</span>
            <span className={s.itemQty}>×{it.qty}</span>
            <span className={s.itemPrice}>{money(it.price_cents * it.qty)}</span>
          </div>
        ))}
      </div>

      <div className={s.totalRow}>
        <span className={s.totalLabel}>Order total</span>
        <span className={s.totalFig}>{money(cart.total_cents)}</span>
      </div>

      {budget > 0 && (
        <div className={s.budget}>
          <div className={s.budgetHead}>
            <span>Against {money(budget)} budget</span>
            <span>{Math.round(pct * 100)}%</span>
          </div>
          <div className={s.meter}>
            <div
              className={`${s.meterFill} ${over ? s.meterOver : ""}`}
              style={{ width: `${Math.max(4, pct * 100)}%` }}
            />
          </div>
          <div className={over ? s.budgetNote : `${s.budgetNote} ${s.budgetNoteOk}`}>
            {over
              ? "Over budget — the run will stop before paying."
              : `${money(budget - cart.total_cents)} of headroom remaining.`}
          </div>
        </div>
      )}
    </div>
  );
}

/* A compact key/value grid used by several small tool bodies. */
export function KeyValueBody({
  rows,
}: {
  rows: { key: string; val: string; mono?: boolean }[];
}) {
  return (
    <div className={c.kvGrid}>
      {rows.map((r, i) => (
        <div key={i} className={c.kvRow}>
          <span className={c.kvKey}>{r.key}</span>
          <span className={r.mono ? c.kvValMono : c.kvVal}>{r.val}</span>
        </div>
      ))}
    </div>
  );
}

/* web_search — the grounded answer + source chips (name + link, favicon if the
   source carries one). Reads as one distinct tool card. */
function SourceChip({
  src,
}: {
  src: { name: string; url: string; snippet?: string; favicon?: string };
}) {
  // If a favicon is provided but fails to load, fall back to the bespoke link
  // glyph rather than leaving a broken-image box.
  const [favBroken, setFavBroken] = useState(false);
  const showFav = Boolean(src.favicon) && !favBroken;
  return (
    <a
      className={c.source}
      href={src.url}
      target="_blank"
      rel="noopener noreferrer"
      title={src.snippet || src.name || src.url}
    >
      {showFav ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          className={c.sourceFav}
          src={src.favicon}
          alt=""
          onError={() => setFavBroken(true)}
        />
      ) : (
        <span className={c.sourceGlyph}>
          <LinkGlyph />
        </span>
      )}
      <span className={c.sourceName}>{src.name || hostname(src.url)}</span>
    </a>
  );
}

export function WebSearchBody({
  answer,
  sources,
}: {
  answer: string | null;
  sources: { name: string; url: string; snippet?: string; favicon?: string }[];
}) {
  // The Linkup answer is light markdown; render its paragraphs with **bold**
  // honoured and nothing else executed (no dangerouslySetInnerHTML).
  const paragraphs = (answer ?? "")
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);

  return (
    <div className={c.stack}>
      {paragraphs.length > 0 && (
        <div className={c.answer}>
          {paragraphs.map((para, i) => (
            <p key={i} className={c.answerP}>
              {renderInlineBold(para)}
            </p>
          ))}
        </div>
      )}
      {sources.length > 0 && (
        <>
          <div className={s.sectionLabel}>Sources</div>
          <div className={c.sources}>
            {sources.map((src, i) => (
              <SourceChip key={i} src={src} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// Render a light-markdown string, honouring only **bold**, as React nodes.
function renderInlineBold(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    return <span key={i}>{part}</span>;
  });
}

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

// A link mark in the shared stroke language (two joined arcs + a break node) —
// no icon-pack chain glyph.
function LinkGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M6.6 9.4 9.4 6.6"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M8.4 5.2l1-1a2.4 2.4 0 0 1 3.4 3.4l-1 1"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M7.6 10.8l-1 1a2.4 2.4 0 0 1-3.4-3.4l1-1"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/* An agent spoken reply — a calm conversational bubble (left-aligned in the
   thread), distinct from a tool card. */
export function AgentBubble({ text }: { text: string }) {
  return (
    <div className={`${c.bubble} ${c.bubbleCalm}`}>
      <p className={c.bubbleText}>{text}</p>
    </div>
  );
}

/* run.done / run.error closing bubble — the assistant's conversational close. */
export function ClosingBubble({ state }: { state: RunState }) {
  const result = state.result as RunDone | null;
  const phase = state.phase;

  if (phase === "declined") {
    return (
      <div className={`${c.bubble} ${c.bubbleCalm}`}>
        <p className={c.bubbleText}>
          Understood — nothing was charged. The card session was released. Say the
          word and I&apos;ll try a different cart or merchant.
        </p>
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div className={`${c.bubble} ${c.bubbleError}`}>
        <p className={c.bubbleText}>
          {state.errorMessage ||
            "The errand stopped before finishing. Nothing was charged without your approval."}
        </p>
      </div>
    );
  }

  // Completed.
  const total = result?.total_cents ?? state.cart?.total_cents;
  const order = state.orderId ?? result?.order_id;
  return (
    <div className={`${c.bubble} ${c.bubbleDone}`}>
      <span className={c.doneSeal}>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M3 8.4l3 3 7-7"
            stroke="currentColor"
            strokeWidth="1.9"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <p className={c.bubbleText}>
        Done — order <strong>{order ?? "placed"}</strong>
        {total != null ? (
          <>
            {" "}
            for <strong>{money(total)}</strong>
          </>
        ) : null}
        .{" "}
        {state.credentialLast4
          ? `Paid with a one-time card ending ${state.credentialLast4}. `
          : ""}
        {state.confirmationOrderId
          ? "Confirmation email caught in the agent inbox."
          : state.inboxAddress
            ? "Watching the agent inbox for the receipt."
            : ""}
      </p>
    </div>
  );
}
