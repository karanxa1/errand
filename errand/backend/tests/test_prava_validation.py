"""The two fields Prava forwards to the card network, and the ways they fail.

Neither of these failures shows up where it is caused, which is the whole reason
they are worth a test file:

  * A reserved-TLD customer email works all the way through — card added, OTP
    delivered, OTP accepted — and then fails at the LAST step, on passkey
    registration, with a generic error. Nothing in that sequence points at the
    email.
  * A merchant url that is not a bare https origin on a real TLD fails at the
    authentication step as a plain 400, before any card is charged, on 100% of
    that merchant's checkouts. A single wrong character does it.

Each case below is one of those. No network.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.brokers import prava as prava_module  # noqa: E402
from app.brokers.prava import PravaPaymentBroker  # noqa: E402
from app.contracts import CartItem, CreateSessionInput, Merchant  # noqa: E402
from app.prava.validate import (  # noqa: E402
    BANNED_TLDS,
    PravaValidationError,
    merchant_origin,
    validate_customer_email,
)

_FAKE_SECRET = "sk_test_placeholder_never_sent"


def _rejects(fn, value: str) -> str:
    try:
        fn(value)
    except PravaValidationError as exc:
        return str(exc)
    raise AssertionError(f"{value!r} should have been rejected")


# ── rule 1: customer email ────────────────────────────────────────────────────

def test_reserved_tld_emails_are_rejected() -> None:
    """The exact addresses that produced PASSKEY_REG_FAILED in sandbox."""
    for email in ("demo@acme.local", "owner@sentinel.local", "demo@macrostack.test"):
        message = _rejects(validate_customer_email, email)
        assert "reserved TLD" in message, message


def test_every_banned_tld_is_actually_enforced() -> None:
    """The list is only worth having if each entry is checked."""
    for tld in BANNED_TLDS:
        _rejects(validate_customer_email, f"someone@shop.{tld}")


def test_example_dot_com_is_valid_but_dot_example_is_not() -> None:
    """The distinction people get backwards. `.com` is a real delegated TLD and
    example.com is a real domain in it; `.example` is reserved."""
    assert validate_customer_email("demo@example.com") == "demo@example.com"
    _rejects(validate_customer_email, "demo@something.example")


def test_real_addresses_survive() -> None:
    for email in ("demo@acme.com", "a7f3c2@agentmail.to", "buyer@shop.co.uk"):
        assert validate_customer_email(email) == email


def test_a_malformed_address_is_rejected_before_the_tld_check() -> None:
    for bad in ("", "not-an-email", "@nohost.com", "two@@at.com"):
        _rejects(validate_customer_email, bad)


# ── rule 2: merchant url ──────────────────────────────────────────────────────

def test_a_full_product_path_is_reduced_to_the_origin() -> None:
    """The live bug this shipped with.

    The demo storefront's url ends in /store/index.html and went to the card
    network verbatim. The shopper genuinely needs that deep link to navigate to,
    so the path is DROPPED here rather than rejected — the network wants the
    merchant's identity, not the page.
    """
    assert (
        merchant_origin("https://deathwishcoffee.com/products/grey-tumbler")
        == "https://deathwishcoffee.com"
    )
    assert (
        merchant_origin("https://errand-frontend.rough-cell-383c.workers.dev/store/index.html")
        == "https://errand-frontend.rough-cell-383c.workers.dev"
    )


def test_a_scheme_typo_is_rejected_not_coerced() -> None:
    """`htttps://zara.com` — one extra character, every checkout dead."""
    message = _rejects(merchant_origin, "htttps://zara.com")
    assert "typo" in message


def test_a_missing_scheme_is_rejected() -> None:
    _rejects(merchant_origin, "www.acme.com")


def test_http_is_rejected() -> None:
    _rejects(merchant_origin, "http://www.acme.com")


def test_reserved_and_implausible_tlds_are_rejected() -> None:
    for url in ("https://www.airshop.demo", "https://shop.local", "https://x.test"):
        _rejects(merchant_origin, url)


def test_a_bare_origin_passes_through_unchanged() -> None:
    assert merchant_origin("https://www.acme.com") == "https://www.acme.com"


def test_the_port_is_dropped_along_with_the_path() -> None:
    """An origin the network can match against a merchant of record has neither."""
    assert merchant_origin("https://shop.acme.com:8443/cart") == "https://shop.acme.com"


# ── the broker actually applies both ──────────────────────────────────────────

class _Recorder:
    sent: dict = {}

    def __init__(self, *a: object, **k: object) -> None:
        pass

    async def __aenter__(self) -> "_Recorder":
        return self

    async def __aexit__(self, *a: object) -> bool:
        return False

    async def post(self, url: str, **kwargs: object):
        _Recorder.sent = kwargs.get("json") or {}

        class _R:
            status_code = 201

            def json(self) -> dict:
                return {"session_id": "s", "iframe_url": "u"}

        return _R()


def _create(merchant_url: str, email: str, **extra):
    broker = PravaPaymentBroker(_FAKE_SECRET, "https://sandbox.api.prava.space")
    original = prava_module.httpx.AsyncClient
    prava_module.httpx.AsyncClient = _Recorder  # type: ignore[assignment]
    try:
        return asyncio.run(
            broker.create_session(
                CreateSessionInput(
                    merchant=Merchant(name="M", url=merchant_url),
                    total_cents=100,
                    user_id="u",
                    user_email=email,
                    items=[CartItem(name="x", qty=1, price_cents=100)],
                    **extra,
                )
            )
        )
    finally:
        prava_module.httpx.AsyncClient = original  # type: ignore[assignment]


def test_create_session_sends_the_origin_not_the_deep_link() -> None:
    _create("https://deathwishcoffee.com/products/grey-tumbler", "buyer@example.com")
    url = _Recorder.sent["purchase_context"][0]["merchant_details"]["url"]
    assert url == "https://deathwishcoffee.com", url


def test_create_session_refuses_a_reserved_tld_email() -> None:
    try:
        _create("https://www.acme.com", "demo@acme.local")
    except PravaValidationError as exc:
        assert "reserved TLD" in str(exc)
    else:
        raise AssertionError("a .local customer email must never reach Prava")


def test_create_session_refuses_a_broken_merchant_url() -> None:
    try:
        _create("htttps://zara.com", "buyer@example.com")
    except PravaValidationError:
        pass
    else:
        raise AssertionError("a scheme typo must never reach Prava")


# ── rule 5: the device id rides along ─────────────────────────────────────────

def test_a_stable_browser_profile_id_is_forwarded() -> None:
    """A fresh id per checkout reads as a new device, forcing another passkey
    registration and burning one of a hard-capped number of token bindings."""
    _create("https://www.acme.com", "buyer@example.com", browser_profile_id="bp_abc")
    assert _Recorder.sent.get("browser_profile_id") == "bp_abc"


def test_no_browser_profile_id_means_the_field_is_omitted() -> None:
    """Never send an empty string — an absent field and a blank one are different
    claims about what we know."""
    _create("https://www.acme.com", "buyer@example.com")
    assert "browser_profile_id" not in _Recorder.sent


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{'FAILED' if failures else 'PASSED'} — {failures} failure(s)")
    sys.exit(1 if failures else 0)
