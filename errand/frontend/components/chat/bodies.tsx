"use client";

/* Tool-card bodies — the per-tool visual renderers that live INSIDE a ToolCard.
   Each reuses the shared stage visual language (budget meter, citation chips,
   item rows, progress rail) but WITHOUT the outer card/step-header, since
   ToolCard already supplies the frame + status. This is the CartPanel/PlanPanel
   content reused inside tool cards, as the brief asks. */

import { useState } from "react";
import type {
  CartResult,
  PurchaseContext,
  RunDone,
} from "@/lib/types";
import type { RunState } from "@/lib/useErrandRun";
import { money, tidySnippet } from "@/lib/format";

/* Shared bits of the stage language, kept as literal class strings so Tailwind
   can see every one of them. */
const STACK = "flex flex-col gap-[14px]";
const SECTION_LABEL =
  "mt-[18px] mb-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-low";

/* Closing / agent bubble — one surface, three tonal lips. */
const BUBBLE = "flex items-start gap-3 rounded-panel px-[18px] py-4 bg-[linear-gradient(180deg,var(--color-ink-150),var(--color-ink-100))]";
const BUBBLE_CALM =
  "shadow-[inset_0_1px_0_rgba(160,240,200,0.06),inset_0_0_0_1px_var(--color-edge)]";
const BUBBLE_DONE =
  "shadow-[inset_0_1px_0_rgba(19,239,147,0.16),inset_0_0_0_1px_var(--color-edge-strong)]";
const BUBBLE_ERROR =
  "shadow-[inset_0_1px_0_rgba(255,122,107,0.14),inset_0_0_0_1px_rgba(255,122,107,0.2)]";
const BUBBLE_TEXT =
  "m-0 text-[14.5px] leading-[1.55] text-body [&_strong]:text-hi [&_strong]:[font-weight:650]";

function Tick() {
  return (
    <svg
      className="mt-[2px] flex-none text-green"
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
      className="flex-none text-green"
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

/* context.loaded — policy: budget headline, rules, Senso citation chips. */
export function PolicyBody({ context }: { context: PurchaseContext }) {
  const [openChip, setOpenChip] = useState<number | null>(0);
  const merchant = context.approved_merchants[0];

  return (
    <div className={STACK}>
      <div className="flex flex-wrap gap-[22px]">
        <div className="flex flex-col gap-[3px]">
          <span className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-low">
            Budget cap
          </span>
          <span className="font-display text-[26px] leading-none tracking-[0.01em] text-brass">
            {money(context.budget_cents)}
          </span>
        </div>
        {merchant && (
          <div className="flex flex-col gap-[3px]">
            <span className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-low">
              Approved merchant
            </span>
            <span className="text-[16px] font-semibold text-hi">{merchant.name}</span>
          </div>
        )}
      </div>

      {context.rules.length > 0 && (
        <ul className="m-0 grid list-none gap-2 p-0">
          {context.rules.map((r, i) => (
            <li
              key={i}
              className="flex items-start gap-2.5 text-[13.5px] leading-[1.4] text-body"
            >
              <Tick />
              {r}
            </li>
          ))}
        </ul>
      )}

      {context.citations.length > 0 && (
        <>
          <div className={SECTION_LABEL}>Grounded in — Senso sources</div>
          <div className="flex flex-wrap gap-2">
            {context.citations.map((cit, i) => (
              <button
                key={i}
                className="inline-flex max-w-full flex-col gap-[3px] rounded-[10px] border-none bg-ink-150 px-[11px] py-2 text-left text-body shadow-[inset_0_0_0_1px_var(--color-edge)] transition-[background-color] duration-[180ms] ease-[ease] hover:bg-ink-200"
                onClick={() => setOpenChip((o) => (o === i ? null : i))}
                aria-expanded={openChip === i}
                type="button"
              >
                <span className="inline-flex items-center gap-[7px] text-[12px] font-semibold text-hi">
                  <DocGlyph />
                  {cit.source}
                </span>
                {openChip === i && (
                  <span className="max-w-[340px] text-[11.5px] leading-[1.4] text-mid">
                    {tidySnippet(cit.snippet)}
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
    <div className={STACK}>
      <div className="grid gap-0.5">
        {cart.items.map((it, i) => (
          <div
            key={i}
            className="grid grid-cols-[1fr_auto_auto] items-center gap-[14px] border-b border-b-edge px-1 py-[11px] last:border-b-0"
          >
            <span className="text-[14px] text-hi">{it.name}</span>
            <span className="min-w-[34px] text-right font-mono text-[12px] text-mid">
              ×{it.qty}
            </span>
            <span className="min-w-[78px] text-right font-mono text-[13.5px] text-body">
              {money(it.price_cents * it.qty)}
            </span>
          </div>
        ))}
      </div>

      <div className="mt-4 flex items-baseline justify-between border-t-[1.5px] border-t-edge-strong pt-4">
        <span className="text-[13px] text-mid">Order total</span>
        <span className="font-display text-[30px] tracking-[0.01em] text-hi">
          {money(cart.total_cents)}
        </span>
      </div>

      {budget > 0 && (
        <div className="mt-4">
          <div className="mb-[7px] flex justify-between text-[12px] text-mid">
            <span>Against {money(budget)} budget</span>
            <span>{Math.round(pct * 100)}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-ink-200 shadow-[inset_0_0_0_1px_var(--color-edge)]">
            <div
              className={
                over
                  ? "h-full rounded-full bg-[linear-gradient(90deg,var(--color-danger-dim),var(--color-danger))] transition-[width] duration-[600ms] ease-[cubic-bezier(0.22,0.8,0.28,1)]"
                  : "h-full rounded-full bg-[linear-gradient(90deg,var(--color-green-dim),var(--color-green))] transition-[width] duration-[600ms] ease-[cubic-bezier(0.22,0.8,0.28,1)]"
              }
              style={{ width: `${Math.max(4, pct * 100)}%` }}
            />
          </div>
          <div
            className={
              over
                ? "mt-[7px] text-[12px] text-brass"
                : "mt-[7px] text-[12px] text-low"
            }
          >
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
    <div className="grid gap-[9px]">
      {rows.map((r, i) => (
        <div key={i} className="flex items-baseline justify-between gap-[14px]">
          <span className="text-[12.5px] text-mid">{r.key}</span>
          <span
            className={
              r.mono
                ? "min-w-0 text-right font-mono text-[12.5px] text-hi [word-break:break-word]"
                : "min-w-0 text-right text-[13.5px] text-hi [word-break:break-word]"
            }
          >
            {r.val}
          </span>
        </div>
      ))}
    </div>
  );
}

/* web_search — the grounded answer + source chips (name + link, favicon if the
   source carries one). Reads as one distinct tool card.

   Source chip — a real link, tonal surface + self-colored lip. Favicon (if the
   source carries one) sits bare, no tile; else a small link glyph in-stroke. */
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
      className="group inline-flex max-w-full items-center gap-[7px] rounded-full bg-ink-100 px-[11px] py-[7px] text-[12px] leading-[1.2] text-body no-underline shadow-[inset_0_0_0_1px_var(--color-edge)] transition-[background-color,color] duration-[180ms] ease-[ease] hover:bg-ink-150 hover:text-hi"
      href={src.url}
      target="_blank"
      rel="noopener noreferrer"
      title={src.snippet || src.name || src.url}
    >
      {showFav ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          className="h-[14px] w-[14px] flex-none rounded-[3px] object-cover"
          src={src.favicon}
          alt=""
          onError={() => setFavBroken(true)}
        />
      ) : (
        <span className="inline-flex flex-none text-low group-hover:text-green-soft">
          <LinkGlyph />
        </span>
      )}
      <span className="min-w-0 max-w-[30ch] truncate">
        {src.name || hostname(src.url)}
      </span>
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
    <div className={STACK}>
      {paragraphs.length > 0 && (
        <div className="flex flex-col gap-2">
          {paragraphs.map((para, i) => (
            <p
              key={i}
              className="m-0 text-[13.5px] leading-[1.55] text-body [&_strong]:text-hi [&_strong]:[font-weight:640]"
            >
              {renderInlineBold(para)}
            </p>
          ))}
        </div>
      )}
      {sources.length > 0 && (
        <>
          <div className={SECTION_LABEL}>Sources</div>
          <div className="flex flex-wrap gap-2">
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
    <div className={`${BUBBLE} ${BUBBLE_CALM}`}>
      <p className={BUBBLE_TEXT}>{text}</p>
    </div>
  );
}

/* run.done / run.error closing bubble — the assistant's conversational close. */
export function ClosingBubble({ state }: { state: RunState }) {
  const result = state.result as RunDone | null;
  const phase = state.phase;

  if (phase === "declined") {
    return (
      <div className={`${BUBBLE} ${BUBBLE_CALM}`}>
        <p className={BUBBLE_TEXT}>
          Understood — nothing was charged. The card session was released. Say the
          word and I&apos;ll try a different cart or merchant.
        </p>
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div className={`${BUBBLE} ${BUBBLE_ERROR}`}>
        <p className={BUBBLE_TEXT}>
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
    <div className={`${BUBBLE} ${BUBBLE_DONE}`}>
      <span className="mt-px inline-flex h-6 w-6 flex-none items-center justify-center rounded-full bg-green text-on-accent shadow-[inset_0_1px_0_rgba(255,255,255,0.25)]">
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
      <p className={BUBBLE_TEXT}>
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
