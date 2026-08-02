"""Negative spend rules must actually exclude the thing they prohibit.

This exists because a live run bought a banned item. Senso's seeded policy says
"Do not purchase energy drinks", but `_is_disallowed` only recognised the shape
"No energy drinks" — so the rule matched nothing, `_select_items` treated the
product as allowed, and the agent put it in a cart that a human was then asked to
approve. A policy rule that silently does nothing is worse than no rule: the
approval screen implies the policy was applied.

Phrasing is prose written by whoever seeded the policy, so the matcher has to
handle the ordinary ways a person says "don't buy this" — not one regex shape.

Runs under pytest, and standalone (`uv run python tests/test_spend_rules.py`).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import run_standalone  # noqa: E402

from app.brokers.shopper import _is_disallowed, _select_items  # noqa: E402

# Verbatim from the live Senso `business` policy (probed 2026-08-02). Kept exact
# so this test fails if the matcher stops handling the real phrasing.
SENSO_RULE = (
    "Also, pantry restocking should be limited to coffee, snack bars, and "
    "sparkling water. Do not purchase energy drinks, and avoid non-approved "
    "brands where a preferred brand exists."
)


def test_do_not_purchase_phrasing_is_honoured() -> None:
    """The exact phrasing the seeded policy uses. This is the regression."""
    assert _is_disallowed("Energy drinks 6 x 250ml", "Voltjolt", [SENSO_RULE])


def test_every_ordinary_negation_phrasing_is_honoured() -> None:
    """A policy is prose. Each of these means the same thing to a human, so each
    must mean the same thing to the matcher.

    The `should not` / `must not` entries are not hypothetical: the live Senso
    answer is LLM-written and varies its wording between responses. One probe said
    "Do not purchase energy drinks" and the next said "you should not purchase
    energy drinks" — the second slipped past a matcher that enumerated only
    do/don't/never, and the run bought the banned item.
    """
    for rule in (
        "No energy drinks",
        "Do not purchase energy drinks",
        "Do not buy energy drinks",
        "Don't order energy drinks",
        "Never buy energy drinks",
        "Avoid energy drinks",
        "Energy drinks are not allowed",
        "Energy drinks are prohibited",
        # Live phrasings observed from Senso:
        "you should not purchase energy drinks",
        "you must not purchase energy drinks",
        "we cannot buy energy drinks",
        "do not include energy drinks",
    ):
        assert _is_disallowed("Energy drinks 6 x 250ml", "Voltjolt", [rule]), rule


def test_the_live_sentence_shape_is_honoured() -> None:
    """The exact live sentence, verbatim. It bundles an ALLOW list and a BAN in
    one sentence, which is what made the ban easy to miss."""
    live = (
        "Also, pantry restocking should be limited to coffee, snack bars, and "
        "sparkling water, and you should not purchase energy drinks. Prefer the "
        "approved/preferred brands when available."
    )
    assert _is_disallowed("Energy drinks 6 x 250ml", "Voltjolt", [live])
    # ...and the products the same sentence ALLOWS must survive it.
    assert not _is_disallowed("Dark roast beans 1kg", "Blue Bottle", [live])
    assert not _is_disallowed("Energy-free snack bars x12", "Clif", [live])
    assert not _is_disallowed("Sparkling water 24 x 330ml", "LaCroix", [live])


def test_an_allowed_product_is_not_swept_up() -> None:
    """The matcher must not over-reach. Coffee is explicitly IN scope for this
    policy, so banning 'energy drinks' cannot take it out."""
    assert not _is_disallowed("Dark roast beans 1kg", "Blue Bottle", [SENSO_RULE])
    assert not _is_disallowed("Sparkling water 24 x 330ml", "LaCroix", [SENSO_RULE])
    assert not _is_disallowed("Oat milk 1L", "Minor Figures", [SENSO_RULE])


def test_a_partial_word_collision_does_not_ban_an_allowed_product() -> None:
    """'energy' appearing inside an unrelated product name must not ban it. A
    naive per-word contains check bans 'Energy-free snack bars' — which is the
    opposite of what the policy says, since snack bars are in scope."""
    assert not _is_disallowed(
        "Energy-free snack bars x12", "Clif", ["Do not purchase energy drinks"]
    )


def test_no_rules_means_nothing_is_banned() -> None:
    assert not _is_disallowed("Energy drinks 6 x 250ml", "Voltjolt", [])


def test_selection_drops_the_banned_product_entirely() -> None:
    """End of the chain: the banned product must never reach the cart, and the
    allowed ones must still be bought."""
    products = [
        {"id": "beans", "brand": "Blue Bottle", "price_cents": 2800, "name": "Dark roast beans 1kg"},
        {"id": "water", "brand": "LaCroix", "price_cents": 1960, "name": "Sparkling water 24 x 330ml"},
        {"id": "energy", "brand": "Voltjolt", "price_cents": 1150, "name": "Energy drinks 6 x 250ml"},
    ]
    plan = _select_items(products, 20000, ["blue bottle", "lacroix"], [SENSO_RULE])
    chosen = {pid for pid, qty in plan if qty > 0}
    assert "energy" not in chosen, plan
    assert "beans" in chosen and "water" in chosen, plan


if __name__ == "__main__":
    sys.exit(1 if run_standalone(dict(globals())) else 0)
