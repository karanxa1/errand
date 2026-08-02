"""Senso policy extraction must not lose the rules that constrain spending.

Two live bugs are pinned here. Both were found by running the real errand and
reading what actually reached the shopper, not by reading the code.

  1. TRUNCATION DROPPED A PROHIBITION. `_extract_rules` kept the first 8 bullet
     lines. The seeded policy renders its budget/vendor/brand bullets first and
     its prose restrictions LAST, so "Do not purchase energy drinks" fell off the
     end. A dropped prohibition is invisible: the run buys the banned item and the
     approval screen still implies the policy was applied.

  2. BRAND PARSING RETURNED A FRAGMENT. `_preferred_brands` split on the
     substring "prefer", so the heading "Preferred brands:" yielded "red brands"
     — a non-existent brand. Every real preference (Blue Bottle, Clif, LaCroix)
     was therefore ignored when ranking the cart.

Runs under pytest, and standalone (`uv run python tests/test_senso_rules.py`).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import run_standalone  # noqa: E402

from app.brokers.senso import _extract_merchants, _extract_rules  # noqa: E402
from app.brokers.shopper import _is_disallowed, _preferred_brands  # noqa: E402

# The live `personal` answer, verbatim (probed 2026-08-02). It labels the vendor
# "Store:" (not "vendor:") and is surrounded by prose, which is what made the
# name extractor swallow a whole sentence.
PERSONAL_ANSWER = """Here's your weekly grocery order based on your preferences:

- **Store:** Demo Pantry Co
- **Budget:** Keep the order at or under **$60 USD**
- **Favourite items / usual weekly groceries:**
  - Oat milk
  - Dark roast coffee
  - Sparkling water

A few notes from your preferences:
- Prefer these favourites when restocking.
- Avoid products with added sugar where possible.
"""

# The live `business` answer, verbatim (probed 2026-08-02). The restriction is the
# LAST line, which is exactly why a head-truncation dropped it.
SENSO_ANSWER = """For **office pantry restocking**:

- **Budget cap:** **$200 USD per order**
- **Approved vendor:** **Demo Pantry Co** — https://demo-pantry.example.com
- **Preferred brands:**
  - **Blue Bottle** (coffee)
  - **Clif** (snack bars)
  - **LaCroix** (sparkling water)

Additional policy notes:
- Use **approved vendors only**
- **Do not purchase energy drinks**
- Buy **pantry staples only**: coffee, snack bars, sparkling water
"""


def test_a_prohibition_survives_extraction() -> None:
    """The whole chain is pointless if the rule never arrives. This is the bug
    that let a real run buy a banned item."""
    rules = _extract_rules(SENSO_ANSWER, SENSO_ANSWER)
    joined = " ".join(rules).lower()
    assert "energy drink" in joined, rules


def test_the_extracted_prohibition_actually_excludes_the_product() -> None:
    """Extraction and matching have to work TOGETHER. Testing them apart is how
    both could pass while the run still bought the item."""
    rules = _extract_rules(SENSO_ANSWER, SENSO_ANSWER)
    assert _is_disallowed("Energy drinks 6 x 250ml", "Voltjolt", rules), rules


def test_prohibitions_are_kept_even_when_the_policy_is_long() -> None:
    """A verbose policy must not be able to push a restriction off the end. The
    restriction is deliberately last here, behind more filler than the cap."""
    answer = "\n".join(
        ["Office pantry policy:"]
        + [f"- Filler policy note number {i} about ordering cadence" for i in range(20)]
        + ["- Do not purchase energy drinks"]
    )
    rules = _extract_rules(answer, answer)
    joined = " ".join(rules).lower()
    assert "energy drink" in joined, rules


def test_allowed_products_are_still_allowed_after_extraction() -> None:
    """Keeping prohibitions must not turn into banning everything."""
    rules = _extract_rules(SENSO_ANSWER, SENSO_ANSWER)
    assert not _is_disallowed("Dark roast beans 1kg", "Blue Bottle", rules)
    assert not _is_disallowed("Sparkling water 24 x 330ml", "LaCroix", rules)
    assert not _is_disallowed("Energy-free snack bars x12", "Clif", rules)


def test_preferred_brands_are_the_real_brands() -> None:
    """'Preferred brands:' is a HEADING, not a brand. Splitting on the substring
    'prefer' turned it into 'red brands' and silently discarded every real
    preference, so the cart was ranked by price alone."""
    rules = _extract_rules(SENSO_ANSWER, SENSO_ANSWER)
    brands = _preferred_brands(rules)
    assert "red brands" not in brands, brands
    joined = " ".join(brands)
    for expected in ("blue bottle", "clif", "lacroix"):
        assert expected in joined, (expected, brands)


def test_preferred_brands_still_parses_inline_prose() -> None:
    """The other real phrasing: a single 'Prefer X/Y/Z' sentence."""
    brands = _preferred_brands(["Prefer Blue Bottle/Clif/LaCroix where available"])
    joined = " ".join(brands)
    for expected in ("blue bottle", "clif", "lacroix"):
        assert expected in joined, (expected, brands)


def test_business_merchant_name_is_clean() -> None:
    """The merchant name goes straight into Prava's merchant_details.name, so it
    must be the vendor, not a sentence. The business answer labels it 'vendor:'."""
    merchants = _extract_merchants(SENSO_ANSWER)
    assert merchants, "no merchant extracted"
    assert merchants[0].name == "Demo Pantry Co", merchants[0].name


def test_personal_store_label_yields_a_clean_name() -> None:
    """The personal answer labels the vendor 'Store:' and wraps it in prose. The
    bug produced 'For groceries I order from Demo Pantry Co' — the extractor
    matched the word 'shop'/lowercase run mid-sentence and swallowed it."""
    merchants = _extract_merchants(PERSONAL_ANSWER)
    assert merchants, "no merchant extracted"
    name = merchants[0].name
    assert name == "Demo Pantry Co", name
    # Guard the exact regression, not just the happy value.
    assert "groceries" not in name.lower(), name
    assert "order from" not in name.lower(), name


def test_a_merchant_name_is_never_a_full_sentence() -> None:
    """A vendor name is a few Title-Case words. Anything with lowercase connective
    tissue ('I', 'from', 'the') is the sentence-swallow bug returning."""
    for answer in (SENSO_ANSWER, PERSONAL_ANSWER):
        name = _extract_merchants(answer)[0].name
        assert len(name.split()) <= 5, name
        assert " i " not in f" {name.lower()} ", name


def test_extraction_still_returns_a_bounded_list() -> None:
    """Unbounded rules would be pasted into an LLM prompt and a browser run, so
    the cap has to stay — it just cannot drop the restrictive lines."""
    answer = "\n".join(
        [f"- Policy note {i} that is long enough to be kept" for i in range(60)]
    )
    rules = _extract_rules(answer, answer)
    assert 0 < len(rules) <= 12, len(rules)


if __name__ == "__main__":
    sys.exit(1 if run_standalone(dict(globals())) else 0)
