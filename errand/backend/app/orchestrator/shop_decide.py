"""The agentic-shop DECISION step, shared by the chat path and the voice relay.

`run_agentic_shop` (app.orchestrator.agentic_shop) drives a bounded surface and,
each turn, calls an injected `decide(intent, catalog, cart, spent_cents,
budget_cents, last_refusal)` to choose the next add/remove/done. That decision is
one OpenAI-compatible model call, and BOTH entry points need the identical one —
the typed chat tool loop and the voice relay's run_errand — so it lives here
instead of inside the chat router. A voice-driven errand and a typed one build
the cart the same way as a result.

The loop itself enforces policy + budget; this only chooses. A malformed or
missing tool call, or any error, becomes `{"action": "done"}` so a confused model
ends the loop cleanly rather than hanging it. reasoning_effort is pinned to the
per-model value the caller passes (gpt-5.6 requires "none" for function tools on
/v1/chat/completions, or it is an HTTP 400 — see the chat router's constant).
"""

from __future__ import annotations

import json

from openai import AsyncOpenAI

from app.config import settings

# The single tool the shop sub-agent is given: pick the next cart action. A
# tightly-bounded surface (add/remove/done + a product_id) — NOT free browsing —
# so the model shapes the cart but cannot navigate anywhere or do anything the
# storefront driver does not expose.
SHOP_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "cart_action",
            "description": (
                "Choose the next action to build the cart toward the user's "
                "request: add one unit of a product, remove one unit, or finish "
                "when the cart matches the request and fits the budget."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "remove", "done"]},
                    "product_id": {
                        "type": "string",
                        "description": "Required for add/remove; the shelf product id.",
                    },
                    "reason": {"type": "string", "description": "One short phrase; optional."},
                },
                "required": ["action"],
            },
        },
    },
]


def make_shop_decide(model_id: str, *, reasoning_effort: str = "none"):
    """Build the `decide` step the agentic shop loop calls each turn."""

    async def decide(
        *, intent, catalog, cart, spent_cents, budget_cents, last_refusal=None
    ) -> dict:
        shelf = "\n".join(
            f"  {p['id']}: {p['name']} ({p['brand']}) ${p['price_cents']/100:.2f}"
            for p in catalog
        )
        in_cart = (
            ", ".join(f"{pid}×{qty}" for pid, qty in cart.items()) if cart else "empty"
        )
        refusal = f"\nLast action was refused: {last_refusal}" if last_refusal else ""
        user = (
            f"Request: {intent}\n\n"
            f"Shelf (id: name (brand) price):\n{shelf}\n\n"
            f"Cart now: {in_cart}\n"
            f"Spent: ${spent_cents/100:.2f} of ${budget_cents/100:.2f} budget."
            f"{refusal}\n\n"
            "Call cart_action for the single best next step. Prefer the products "
            "the request names and the budget allows; call done when the cart "
            "satisfies the request or nothing else fits."
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You build a shopping cart one action at a time by calling "
                    "cart_action. You may only add or remove products that are on "
                    "the shelf. Stay within budget. Finish with done as soon as "
                    "the cart fits the request — do not pad it."
                ),
            },
            {"role": "user", "content": user},
        ]
        try:
            async with AsyncOpenAI(api_key=settings.openai_api_key) as client:
                completion = await client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    tools=SHOP_TOOL,
                    tool_choice="required",
                    reasoning_effort=reasoning_effort,
                    max_completion_tokens=256,
                )
            choice = completion.choices[0] if completion.choices else None
            calls = getattr(choice.message, "tool_calls", None) if choice else None
            if not calls:
                return {"action": "done"}
            args = json.loads(calls[0].function.arguments or "{}")
            action = str(args.get("action") or "done").lower()
            if action not in ("add", "remove", "done"):
                return {"action": "done"}
            return {
                "action": action,
                "product_id": str(args.get("product_id") or ""),
                "reason": str(args.get("reason") or ""),
            }
        except Exception:  # noqa: BLE001 — a decision failure ends the loop, never the errand
            return {"action": "done"}

    return decide
