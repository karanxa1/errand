"""Agentic shop loop — the LLM DRIVES the cart instead of a fixed budget-filler.

The old shopper ran one predetermined pass: `_select_items` filled the cart to
the policy budget and that was that. The LLM's intent never reached it, so "add
the dark roast", "drop the tea", "make it cheaper" all produced the same cart.

Here the model instead works a bounded tool surface against whatever store the
run is shopping — observe the shelf and the current cart, add a product, remove a
product, finish — and every action is checked against the SAME policy and spend
rules the deterministic path enforced. The model chooses WHAT to buy; it can
never loosen a rule:

  * a disallowed product (`_is_disallowed`) is refused, not added;
  * an add that would push the running total over the effective budget
    (min(policy, user cap), already applied to `context.budget_cents` upstream)
    is refused;
  * the loop is bounded by a hard action cap so a confused model cannot spin.

The surface is abstract (`ShopSurface`) so this loop is driver-agnostic: the
Playwright storefront driver implements it against the real DOM, and tests drive
a fake in-memory surface. The loop returns the chosen (product_id, qty) plan; the
concrete shopper turns that into the real clicks + the authoritative DOM total,
exactly as before, so the money-path invariants downstream do not move.

This module holds NO Playwright and NO OpenAI import — the caller injects both a
`ShopSurface` and a `decide` callable — so it is unit-testable with neither a
browser nor a network.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from app.brokers.policy import _is_disallowed


class Product:
    """One shelf item the loop can reason about. Plain object, not pydantic —
    it is internal to the loop and built from whatever the surface observed."""

    __slots__ = ("id", "name", "brand", "price_cents")

    def __init__(self, id: str, name: str, brand: str, price_cents: int) -> None:
        self.id = id
        self.name = name
        self.brand = brand
        self.price_cents = price_cents

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "brand": self.brand,
            "price_cents": self.price_cents,
        }


class ShopSurface(Protocol):
    """The bounded set of things the agent can do to a store. A storefront driver
    implements it over the real DOM; a fake implements it in memory for tests."""

    async def catalog(self) -> list[Product]:
        """Every product currently on the shelf."""
        ...

    async def cart(self) -> dict[str, int]:
        """The current cart as {product_id: qty}, read from the store itself."""
        ...

    async def add(self, product_id: str) -> None:
        """Put one unit of a product into the cart."""
        ...

    async def remove(self, product_id: str) -> None:
        """Take one unit of a product back out."""
        ...


# The model's decision for one step. `action` is one of add/remove/done; `product_id`
# is required for add/remove. Returned by the injected `decide` callable so the
# loop itself stays free of any LLM/transport detail.
Decision = dict  # {"action": "add"|"remove"|"done", "product_id"?: str, "reason"?: str}

# Injected decision function: given the intent, the catalog, the current cart, and
# the remaining budget, return the next Decision. Async so the real one can call
# the model; the test one is synchronous logic wrapped in async.
DecideFn = Callable[..., Awaitable[Decision]]

# Notify the caller of each step so it can stream a caption + capture a frame.
# (step_label, detail) -> awaitable. Optional.
OnStep = Callable[[str, str], Awaitable[None]]


class AgenticShopError(RuntimeError):
    """The loop could not produce a usable plan (e.g. the model never finished)."""


def _line_total(catalog: dict[str, Product], cart: dict[str, int]) -> int:
    return sum(catalog[pid].price_cents * qty for pid, qty in cart.items() if pid in catalog)


async def run_agentic_shop(
    surface: ShopSurface,
    *,
    intent: str,
    budget_cents: int,
    rules: list[str],
    decide: DecideFn,
    max_actions: int = 16,
    on_step: OnStep | None = None,
) -> list[tuple[str, int]]:
    """Drive the surface with `decide` until it says done (or the action cap hits).

    Returns the chosen plan as [(product_id, qty), ...]. Every add is checked
    against `rules` (policy) and `budget_cents` (already min(policy, user cap));
    a refused add is reported to the model on the next turn via the observation,
    not silently dropped, so it can pick something cheaper/allowed instead.

    The store is the source of truth for the cart: after each accepted action the
    cart is re-read from the surface, so the plan can never drift from what is
    actually in the store (the same discipline as reading #cart-total from the
    DOM rather than trusting local bookkeeping).
    """
    products = await surface.catalog()
    catalog = {p.id: p for p in products}

    async def note(step: str, detail: str) -> None:
        if on_step is not None:
            await on_step(step, detail)

    last_refusal: str | None = None

    for _ in range(max_actions):
        cart = await surface.cart()
        spent = _line_total(catalog, cart)

        decision = await decide(
            intent=intent,
            catalog=[p.as_dict() for p in products],
            cart=dict(cart),
            spent_cents=spent,
            budget_cents=budget_cents,
            last_refusal=last_refusal,
        )
        last_refusal = None
        action = str(decision.get("action") or "").lower()

        if action == "done":
            await note("shop.done", "Agent finished building the cart.")
            break

        pid = str(decision.get("product_id") or "")
        if action not in ("add", "remove") or pid not in catalog:
            # A malformed or unknown-product decision is not fatal — tell the
            # model and let it try again rather than aborting the whole errand.
            last_refusal = f"Ignored an invalid action {action!r} for {pid!r}."
            await note("shop.invalid", last_refusal)
            continue

        product = catalog[pid]

        if action == "add":
            if _is_disallowed(product.name, product.brand, rules):
                last_refusal = f"{product.name} is not allowed by policy; did not add it."
                await note("shop.refused", last_refusal)
                continue
            if spent + product.price_cents > budget_cents:
                last_refusal = (
                    f"Adding {product.name} (${product.price_cents/100:.2f}) would exceed "
                    f"the ${budget_cents/100:.2f} budget; did not add it."
                )
                await note("shop.refused", last_refusal)
                continue
            await surface.add(pid)
            await note("shop.add", f"Added {product.name}.")
        else:  # remove
            if (cart.get(pid) or 0) <= 0:
                last_refusal = f"{product.name} is not in the cart; nothing to remove."
                await note("shop.refused", last_refusal)
                continue
            await surface.remove(pid)
            await note("shop.remove", f"Removed {product.name}.")

    # Final plan is whatever actually ended up in the store's cart.
    final_cart = await surface.cart()
    return [(pid, qty) for pid, qty in final_cart.items() if qty > 0 and pid in catalog]
