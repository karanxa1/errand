"""The agentic shop loop lets the model build the cart, but never break a rule.

The loop is the fix for "the agent can't add/remove products or react to the
shelf". It hands the model a bounded surface (observe/add/remove/done) and checks
every add against the SAME policy + budget the deterministic path enforced. These
tests pin the safety envelope, because this is the code that decides what a human
is then asked to pay for:

  * add/remove/done actually move the cart;
  * a policy-banned product is refused, not added;
  * an add that would breach the (already-capped) budget is refused;
  * a model that never says "done" is bounded by the action cap.

No browser and no LLM: a fake in-memory surface stands in for Playwright, and a
scripted `decide` stands in for the model.

Runs under pytest, and standalone (`uv run python tests/test_agentic_shop.py`).
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import run_standalone  # noqa: E402

from app.orchestrator.agentic_shop import Product, run_agentic_shop  # noqa: E402

# A shelf with a preferred coffee, a cheap tea, and a policy-banned energy drink.
_SHELF = [
    Product("beans", "Dark roast beans 1kg", "Blue Bottle", 2800),
    Product("tea", "English breakfast tea x40", "Twinings", 720),
    Product("energy", "Energy drinks 6 x 250ml", "Voltjolt", 1150),
]
_BAN = ["Do not purchase energy drinks"]


class _FakeSurface:
    """In-memory stand-in for the Playwright storefront driver."""

    def __init__(self, shelf: list[Product]) -> None:
        self._shelf = shelf
        self._cart: dict[str, int] = {}
        self.adds: list[str] = []
        self.removes: list[str] = []

    async def catalog(self) -> list[Product]:
        return list(self._shelf)

    async def cart(self) -> dict[str, int]:
        return dict(self._cart)

    async def add(self, product_id: str) -> None:
        self._cart[product_id] = self._cart.get(product_id, 0) + 1
        self.adds.append(product_id)

    async def remove(self, product_id: str) -> None:
        if self._cart.get(product_id, 0) > 0:
            self._cart[product_id] -= 1
            if self._cart[product_id] == 0:
                del self._cart[product_id]
        self.removes.append(product_id)


def _scripted(steps: list[dict]):
    """A `decide` that plays a fixed list of decisions, then says done."""
    seq = list(steps)

    async def decide(**_kwargs) -> dict:
        return seq.pop(0) if seq else {"action": "done"}

    return decide


def _run(surface, decide, *, budget_cents=20000, rules=None, max_actions=16):
    return asyncio.run(
        run_agentic_shop(
            surface,
            intent="buy pantry things",
            budget_cents=budget_cents,
            rules=rules or [],
            decide=decide,
            max_actions=max_actions,
        )
    )


def test_add_and_done_puts_the_chosen_product_in_the_plan() -> None:
    surface = _FakeSurface(_SHELF)
    plan = _run(surface, _scripted([{"action": "add", "product_id": "beans"}]))
    assert dict(plan) == {"beans": 1}
    assert surface.adds == ["beans"]


def test_remove_takes_a_product_back_out() -> None:
    surface = _FakeSurface(_SHELF)
    plan = _run(
        surface,
        _scripted(
            [
                {"action": "add", "product_id": "beans"},
                {"action": "add", "product_id": "tea"},
                {"action": "remove", "product_id": "tea"},
            ]
        ),
    )
    assert dict(plan) == {"beans": 1}
    assert "tea" in surface.removes


def test_a_policy_banned_product_is_refused_not_added() -> None:
    """The whole point of the envelope: the model asking for a banned item must
    not be able to put it in a cart a human then approves."""
    surface = _FakeSurface(_SHELF)
    plan = _run(
        surface,
        _scripted([{"action": "add", "product_id": "energy"}]),
        rules=_BAN,
    )
    assert dict(plan) == {}, "banned product reached the cart"
    assert "energy" not in surface.adds


def test_an_add_over_budget_is_refused() -> None:
    """The budget passed here is already min(policy, user cap). An add that would
    breach it is refused, so the loop can never spend past the ceiling."""
    surface = _FakeSurface(_SHELF)
    # $8 budget: the $28 beans do not fit, the $7.20 tea does.
    plan = _run(
        surface,
        _scripted(
            [
                {"action": "add", "product_id": "beans"},  # refused, over budget
                {"action": "add", "product_id": "tea"},  # fits
            ]
        ),
        budget_cents=800,
    )
    assert dict(plan) == {"tea": 1}
    assert "beans" not in surface.adds


def test_the_action_cap_bounds_a_model_that_never_finishes() -> None:
    """A confused model that keeps adding must not loop forever; the cap stops it
    and whatever is in the cart at that point is the plan."""
    surface = _FakeSurface(_SHELF)
    # Always adds tea, never says done. Cap at 3 → 3 teas, then stop.
    async def always_add_tea(**_kwargs) -> dict:
        return {"action": "add", "product_id": "tea"}

    plan = _run(surface, always_add_tea, max_actions=3)
    assert dict(plan) == {"tea": 3}


def test_an_invalid_action_is_skipped_not_fatal() -> None:
    surface = _FakeSurface(_SHELF)
    plan = _run(
        surface,
        _scripted(
            [
                {"action": "frobnicate", "product_id": "beans"},  # nonsense
                {"action": "add", "product_id": "nope"},  # unknown product
                {"action": "add", "product_id": "tea"},  # real
            ]
        ),
    )
    assert dict(plan) == {"tea": 1}


if __name__ == "__main__":
    sys.exit(1 if run_standalone(dict(globals())) else 0)
