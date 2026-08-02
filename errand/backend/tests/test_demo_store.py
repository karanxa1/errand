"""The demonstration storefront and the unroutable-merchant resolution.

Two things are pinned here, and both exist because an errand could never
complete without them:

  1. `resolve_merchant` swaps ONLY the hosts configured as unroutable, keeps the
     policy's merchant NAME, and reports the original so the caller can record
     the substitution in the audit trail.
  2. The storefront under errand/frontend/public/store/ actually satisfies the
     DOM contract that app/brokers/shopper.py drives. The shopper is a real
     browser reading real selectors, so a renamed hook there is a silent,
     multi-minute timeout at run time rather than a test failure — unless it is
     pinned here.

Runs under pytest, and standalone (`uv run python tests/test_demo_store.py`).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import run_standalone  # noqa: E402

from app.config import settings  # noqa: E402
from app.contracts import Merchant  # noqa: E402
from app.brokers.shopper import _checkout_url_for  # noqa: E402
from app.orchestrator.run_errand import resolve_merchant  # noqa: E402

_STORE_DIR = (
    Path(__file__).resolve().parents[2] / "frontend" / "public" / "store"
)
_INDEX = _STORE_DIR / "index.html"
_CHECKOUT = _STORE_DIR / "checkout.html"


# ── unroutable-merchant resolution ────────────────────────────────────────────

def test_the_seeded_policy_host_is_treated_as_unroutable() -> None:
    """`example.com` is IANA-reserved, so the seeded vendor URL can never be a
    store. If this host ever drops out of the config the errand goes back to
    timing out in build_cart with no 4xx anywhere to explain it."""
    assert "demo-pantry.example.com" in settings.unroutable_hosts


def test_an_unroutable_merchant_resolves_to_the_demo_store() -> None:
    original = "https://demo-pantry.example.com"
    resolved, substituted_from = resolve_merchant(
        Merchant(name="Demo Pantry Co", url=original)
    )
    # The URL moves...
    assert resolved.url == settings.demo_store_url
    # ...the NAME does not. Senso remains the source of truth for who is
    # approved; this only changes where we shop.
    assert resolved.name == "Demo Pantry Co"
    # And the original is reported, so run_errand can record the substitution.
    assert substituted_from == original


def test_a_routable_merchant_is_left_completely_alone() -> None:
    """The rewrite must be narrow. A real merchant URL passing through here
    unchanged is what stops this from quietly redirecting genuine spend."""
    merchant = Merchant(name="Real Store", url="https://shop.example.org/catalog")
    resolved, substituted_from = resolve_merchant(merchant)
    assert resolved.url == "https://shop.example.org/catalog"
    assert resolved.name == "Real Store"
    # None is the signal "no substitution", which suppresses the audit event.
    assert substituted_from is None


def test_substitution_is_reported_so_the_audit_trail_can_stay_honest() -> None:
    """The Prava session is pinned to the RESOLVED merchant, so the card is
    scoped to it. Failing to report the swap would make the record imply the
    policy named the URL we actually charged against."""
    _, substituted = resolve_merchant(
        Merchant(name="Demo Pantry Co", url="https://demo-pantry.example.com/x")
    )
    assert substituted == "https://demo-pantry.example.com/x"


# ── the storefront satisfies the shopper's DOM contract ───────────────────────

def test_the_storefront_files_exist_where_the_worker_serves_them() -> None:
    """Under frontend/public/ so they deploy with the existing Worker as a real
    HTTPS origin. A localhost store would force the shopper's LOCAL browser path
    and never exercise the deployed remote-browser path."""
    assert _INDEX.is_file(), _INDEX
    assert _CHECKOUT.is_file(), _CHECKOUT


def test_catalog_exposes_every_attribute_build_cart_reads() -> None:
    """build_cart's readiness probe waits for [data-product-id], then reads
    data-brand, data-price-cents and [data-name] off each product."""
    html = _INDEX.read_text(encoding="utf-8")
    for hook in (
        "data-product-id",
        "data-brand",
        "data-price-cents",
        "data-name",
    ):
        assert hook in html, hook


def test_catalog_exposes_add_buttons_and_a_machine_readable_total() -> None:
    """The shopper clicks button[data-add="<id>"] and reads
    #cart-total's data-total-cents — never the rendered price text."""
    html = _INDEX.read_text(encoding="utf-8")
    assert 'data-add=' in html
    assert 'id="cart-total"' in html
    assert "data-total-cents" in html


def test_catalog_exposes_remove_buttons_and_per_line_qty() -> None:
    """The agentic shop loop takes a unit back out with
    button[data-remove="<id>"], and reads the current cart off each
    li[data-line="<id>"]'s data-qty. A renamed hook here is a silent multi-minute
    timeout at run time, so it is pinned exactly like the add/total contract."""
    html = _INDEX.read_text(encoding="utf-8")
    assert "data-remove=" in html
    assert "data-line" in html
    assert "data-qty" in html
    # The remove handler must actually decrement/delete, not just exist as markup.
    assert "function removeFromCart" in html


def test_catalog_prices_are_integer_cents() -> None:
    """Cents everywhere: a float price would drift against the Prava session
    amount, and the session pins the total the card is scoped to."""
    html = _INDEX.read_text(encoding="utf-8")
    prices = re.findall(r"price_cents:\s*(\d+)", html)
    assert prices, "catalog defines no prices"
    for value in prices:
        assert value.isdigit(), value


def test_catalog_includes_a_product_the_policy_rules_must_exclude() -> None:
    """The seeded policy says 'do not purchase energy drinks'. Without a real
    energy drink on the shelf, _is_disallowed is never exercised and the demo
    proves nothing about the rule being honoured."""
    html = _INDEX.read_text(encoding="utf-8").lower()
    assert "energy drinks" in html


def test_checkout_exposes_every_field_complete_checkout_fills() -> None:
    """complete_checkout waits for #checkout-form, then fills the credential
    fields and clicks #place-order."""
    html = _CHECKOUT.read_text(encoding="utf-8")
    for hook in (
        'id="checkout-form"',
        'id="card-number"',
        'id="expiry-month"',
        'id="expiry-year"',
        'id="cvv"',
        'id="place-order"',
    ):
        assert hook in html, hook


def test_checkout_signals_completion_the_way_the_shopper_waits_for_it() -> None:
    """The shopper waits for `#confirmation.show` and then reads
    data-order-id, falling back to an ORD-\\d+ match in the copy."""
    html = _CHECKOUT.read_text(encoding="utf-8")
    assert 'id="confirmation"' in html
    assert "data-order-id" in html
    # The `show` class is the completion signal, so it must be applied in script.
    assert 'classList.add("show")' in html
    # And the id shape must match the shopper's regex fallback.
    assert '"ORD-"' in html


def test_checkout_reads_the_total_the_shopper_pins_into_the_url() -> None:
    """The shopper opens checkout in a FRESH context with no shared storage, so
    the amount can only arrive via ?total_cents= (see _checkout_url_for)."""
    html = _CHECKOUT.read_text(encoding="utf-8")
    assert "total_cents" in html


def test_the_derived_checkout_url_matches_the_shipped_filename() -> None:
    """_checkout_url_for replaces the last path segment with checkout.html. If
    the file were named anything else, every checkout would 404 after a
    successful cart — the worst possible place to fail."""
    derived = _checkout_url_for(settings.demo_store_url, 7100)
    assert derived.endswith("/store/checkout.html?total_cents=7100"), derived
    assert _CHECKOUT.name == "checkout.html"


def test_the_demo_store_url_is_itself_routable() -> None:
    """The whole point of the resolution is to reach a host that exists. A demo
    URL on a reserved domain would reintroduce the original bug."""
    from urllib.parse import urlparse

    host = (urlparse(settings.demo_store_url).hostname or "").lower()
    assert host, settings.demo_store_url
    assert host not in settings.unroutable_hosts
    for reserved in (".example.com", ".invalid", ".test", ".localhost"):
        assert not host.endswith(reserved), host


if __name__ == "__main__":
    sys.exit(1 if run_standalone(dict(globals())) else 0)
