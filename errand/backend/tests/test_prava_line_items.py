"""What the human SEES on Prava's card page must equal what they are charged.

⚠️ Prava's payment iframe renders only `product_details[0]`. Verified against the
sandbox, not assumed: a session carrying two line items (Oat milk 1L $3.90 +
Dark roast beans 1kg $28.00, `total_amount` $31.90) renders exactly one row —
"Oat milk 1L … $3.90" — and the second is absent from the page entirely.

The charge was never wrong; the card is minted against `total_amount`. But the
approval happens ON THAT PAGE, and it showed $3.90 next to our own $31.90 summary.
These tests pin the fix: whatever else is sent, the FIRST line item's price is the
total being authorized.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conftest  # noqa: E402

from app.brokers.prava import PravaPaymentBroker  # noqa: E402
from app.contracts import CartItem  # noqa: E402

_details = PravaPaymentBroker._product_details


def _first_price_cents(items: list[CartItem], total_cents: int) -> int:
    """The number the user will actually read on the card page, in cents."""
    details = _details(items, total_cents)
    assert details, "product_details must never be empty"
    first = details[0]
    # The row shows unit_price; quantity is rendered separately as "Qty: n", so a
    # quantity above 1 would still display the UNIT price as the money figure.
    assert first["quantity"] == 1, (
        "the visible line must be a single unit, or the iframe shows a unit price "
        "that is smaller than the total"
    )
    return round(float(first["unit_price"]) * 100)


def test_the_visible_line_equals_the_total_for_the_screenshot_cart() -> None:
    """The exact cart from the bug report: $3.90 + $28.00 = $31.90.

    Before the fix this displayed $3.90 on the page where the user approves.
    """
    items = [
        CartItem(name="Oat milk 1L", qty=1, price_cents=390),
        CartItem(name="Dark roast beans 1kg", qty=1, price_cents=2800),
    ]
    assert _first_price_cents(items, 3190) == 3190


def test_the_visible_line_names_what_is_in_the_order() -> None:
    """Consolidating must not reduce the order to an opaque number."""
    items = [
        CartItem(name="Oat milk 1L", qty=1, price_cents=390),
        CartItem(name="Dark roast beans 1kg", qty=1, price_cents=2800),
    ]
    description = _details(items, 3190)[0]["description"]
    assert "Oat milk 1L" in description
    assert "Dark roast beans 1kg" in description
    assert "2 items" in description


def test_a_single_item_keeps_its_real_name() -> None:
    """When one line already tells the truth, say the product name rather than a
    generated summary — it is strictly more informative."""
    items = [CartItem(name="Oat milk 1L", qty=1, price_cents=390)]
    details = _details(items, 390)
    assert details[0]["description"] == "Oat milk 1L"
    assert _first_price_cents(items, 390) == 390


def test_a_single_item_with_shipping_still_shows_the_total() -> None:
    """The wallet quote includes shipping and tax, so even a one-item cart can have
    a total above its line price. This is the case that has nothing to do with
    multiple items and would still have shown a smaller number."""
    items = [CartItem(name="Oat milk 1L", qty=1, price_cents=390)]
    assert _first_price_cents(items, 685) == 685


def test_a_single_item_bought_several_times_shows_the_total_not_the_unit() -> None:
    """`quantity: 6` renders as "Qty: 6" beside a $3.90 unit price — so the money on
    screen would read $3.90 for a $23.40 charge."""
    items = [CartItem(name="Oat milk 1L", qty=6, price_cents=390)]
    assert _first_price_cents(items, 2340) == 2340


def test_many_items_never_overflow_the_description_limit() -> None:
    """Prava caps the field; the count must survive even when the names are cut."""
    items = [
        CartItem(name=f"A very long product name number {i}", qty=1, price_cents=100)
        for i in range(40)
    ]
    details = _details(items, 4000)
    assert len(details[0]["description"]) <= 255
    assert details[0]["description"].startswith("40 items")
    assert _first_price_cents(items, 4000) == 4000


def test_an_empty_cart_still_produces_a_valid_honest_line() -> None:
    """Defensive: the orchestrator refuses an empty cart long before this, but a
    line item that priced at 0 while charging a total would be the same class of
    lie this module exists to prevent."""
    assert _first_price_cents([], 1200) == 1200


def test_a_nameless_item_does_not_produce_a_dangling_separator() -> None:
    items = [
        CartItem(name="", qty=1, price_cents=390),
        CartItem(name="", qty=1, price_cents=2800),
    ]
    description = _details(items, 3190)[0]["description"]
    assert not description.rstrip().endswith("—"), description
    assert "2 items" in description


def test_rounding_is_exact_at_awkward_amounts() -> None:
    """Float formatting must not shave a cent off the authorized amount."""
    for total in (1, 5, 99, 105, 3190, 99999, 100000, 123456):
        items = [
            CartItem(name="a", qty=1, price_cents=1),
            CartItem(name="b", qty=1, price_cents=2),
        ]
        assert _first_price_cents(items, total) == total, total


if __name__ == "__main__":
    raise SystemExit(conftest.run_standalone(dict(globals())))
