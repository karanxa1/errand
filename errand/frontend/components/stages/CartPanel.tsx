"use client";

import type { CartResult } from "@/lib/types";
import { money } from "@/lib/format";
import s from "./stages.module.css";

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
    <div className={s.card}>
      <div className={s.head}>
        <span className={s.stepNo}>02 · CART</span>
        <h2 className={s.title}>What the agent parked at checkout</h2>
        <span className={s.sub}>
          {cart.items.length} item{cart.items.length === 1 ? "" : "s"}
        </span>
      </div>

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
