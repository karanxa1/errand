"use client";

import type { CartResult } from "@/lib/types";
import { money } from "@/lib/format";

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

export default function CartPanel({
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
    <div className={CARD}>
      <div className={HEAD}>
        <span className={STEP_NO}>02 · CART</span>
        <h2 className={TITLE}>What the agent parked at checkout</h2>
        <span className={SUB}>
          {cart.items.length} item{cart.items.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="grid gap-0.5">
        {cart.items.map((it, i) => (
          <div
            key={i}
            className="grid grid-cols-[1fr_auto_auto] items-center gap-[14px] px-1 py-[11px] border-b border-edge last:border-b-0"
          >
            <span className="text-hi text-[14px]">{it.name}</span>
            <span className="font-mono text-[12px] text-mid min-w-[34px] text-right">
              ×{it.qty}
            </span>
            <span className="font-mono text-[13.5px] text-body min-w-[78px] text-right">
              {money(it.price_cents * it.qty)}
            </span>
          </div>
        ))}
      </div>

      <div className="flex items-baseline justify-between mt-4 pt-4 border-t-[1.5px] border-edge-strong">
        <span className="text-[13px] text-mid">Order total</span>
        <span className="font-display text-[30px] text-hi tracking-[0.01em]">
          {money(cart.total_cents)}
        </span>
      </div>

      {budget > 0 && (
        <div className="mt-4">
          <div className="flex justify-between text-[12px] text-mid mb-[7px]">
            <span>Against {money(budget)} budget</span>
            <span>{Math.round(pct * 100)}%</span>
          </div>
          <div className="h-2 rounded-full bg-ink-200 shadow-[inset_0_0_0_1px_var(--color-edge)] overflow-hidden">
            <div
              className={`h-full rounded-full transition-[width] duration-[600ms] ease-[cubic-bezier(0.22,0.8,0.28,1)] ${
                over
                  ? "bg-[image:linear-gradient(90deg,var(--color-danger-dim),var(--color-danger))]"
                  : "bg-[image:linear-gradient(90deg,var(--color-green-dim),var(--color-green))]"
              }`}
              style={{ width: `${Math.max(4, pct * 100)}%` }}
            />
          </div>
          <div
            className={`mt-[7px] text-[12px] ${over ? "text-brass" : "text-low"}`}
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
