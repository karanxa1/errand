"""Voice ticket regressions — the thing standing between an anonymous socket and
real spend.

Runs under pytest if it is installed, and standalone (`uv run python
tests/test_voice_tickets.py`) if it is not, since the backend does not currently
carry a test runner dependency.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.voice.tickets import (  # noqa: E402
    MAX_OUTSTANDING_TICKETS,
    TICKET_TTL_S,
    clear_tickets,
    issue_ticket,
    redeem_ticket,
)


def test_redeem_returns_the_issuing_identity() -> None:
    clear_tickets()
    ticket, expires_in = issue_ticket("user-123", "buyer@example.com", now=0.0)
    assert expires_in == TICKET_TTL_S
    redeemed = redeem_ticket(ticket, now=1.0)
    assert redeemed is not None
    # The relay derives user_id/user_email for run_errand from exactly this.
    assert redeemed.user_id == "user-123"
    assert redeemed.user_email == "buyer@example.com"


def test_ticket_is_single_use() -> None:
    clear_tickets()
    ticket, _ = issue_ticket("user-123", "buyer@example.com", now=0.0)
    assert redeem_ticket(ticket, now=1.0) is not None
    # A replay — e.g. the ticket recovered from an access log — must fail.
    assert redeem_ticket(ticket, now=1.0) is None


def test_expired_ticket_is_rejected() -> None:
    clear_tickets()
    ticket, _ = issue_ticket("user-123", "buyer@example.com", now=0.0)
    assert redeem_ticket(ticket, now=TICKET_TTL_S + 0.1) is None


def test_absent_or_unknown_ticket_is_rejected() -> None:
    clear_tickets()
    assert redeem_ticket(None) is None
    assert redeem_ticket("") is None
    assert redeem_ticket("not-a-ticket-anyone-issued") is None


def test_expired_tickets_are_swept_not_accumulated() -> None:
    clear_tickets()
    from app.voice import tickets as store

    for i in range(5):
        issue_ticket(f"user-{i}", f"u{i}@example.com", now=0.0)
    assert len(store._tickets) == 5
    # Any later traffic (a mint or a redeem) sweeps the dead ones, so the store
    # is bounded by LIVE tickets rather than by tickets ever issued.
    issue_ticket("user-late", "late@example.com", now=TICKET_TTL_S + 1)
    assert len(store._tickets) == 1


def test_outstanding_tickets_are_capped() -> None:
    clear_tickets()
    from app.voice import tickets as store

    for i in range(MAX_OUTSTANDING_TICKETS + 10):
        issue_ticket(f"user-{i}", f"u{i}@example.com", now=0.0)
    assert len(store._tickets) <= MAX_OUTSTANDING_TICKETS
    clear_tickets()


def test_tickets_are_opaque_random_not_jwts() -> None:
    clear_tickets()
    a, _ = issue_ticket("user-123", "buyer@example.com", now=0.0)
    b, _ = issue_ticket("user-123", "buyer@example.com", now=0.0)
    assert a != b
    # A JWT would leak the user id into the URL query string (and into every
    # access log that records it); an opaque token carries nothing.
    assert "." not in a
    assert "user-123" not in a
    assert len(a) >= 40


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
