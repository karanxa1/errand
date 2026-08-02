"""One-shot, short-lived tickets that authenticate the voice WebSocket.

The browser WebSocket API cannot set an Authorization header, so /api/voice/ws
cannot use the bearer dependency every other spending route uses. Instead the
client POSTs /api/voice/ticket over authenticated HTTP, receives an opaque
ticket, and hands it to the socket as ?ticket=... The relay redeems it before it
will talk to Deepgram, so an anonymous socket can no longer burn Deepgram +
OpenAI credits or reach run_errand (real spend).

Why the ticket is shaped the way it is:
  - Opaque random (secrets.token_urlsafe(32)), NEVER a JWT. It travels in a URL
    query string, and query strings land in access logs, proxy logs and error
    trackers. A leaked JWT would still be a valid bearer token for the whole
    session; a leaked ticket is worthless the moment it is used or 60s old.
  - Single-use. Redeeming deletes it, so a replay from a log line is rejected.
  - 60s TTL. Long enough that the ticket survives a slow mic-permission prompt
    between the mint and the socket opening, short enough to be useless later.
  - Carries the issuing user's id + email, so the relay knows WHO is on the wire
    and can attribute the errand to them instead of the old hardcoded "u_demo".
    Copying the email at mint time keeps the DB off the socket-accept path.

⚠️ SCALING CONSTRAINT — THIS REQUIRES EXACTLY ONE PROCESS.
The ticket lives in THIS process's heap, and the mint (POST /api/voice/ticket)
and the redeem (the WS handshake) are two separate connections, so they must be
routed to the same process for the ticket to ever be found. Consequences of
scaling out or restarting:
  - replicas > 1: the socket lands on a replica that never minted that ticket,
    redeem returns None, and the relay closes 4401. The user sees "Voice needs
    you to be signed in." even though they just signed in — an auth failure that
    is really a routing failure.
  - uvicorn --workers > 1 (or gunicorn with multiple workers) breaks this the
    same way, for the same reason. Keep it single-worker.
  - a deploy / crash between mint and redeem invalidates outstanding tickets;
    the user simply taps the orb again and a new one is minted, so this is the
    benign case.
The deployment is pinned to min=max=1 replica precisely to satisfy this. (The
approval gate, which used to share this exact in-memory constraint, is now
DB-backed and no longer does — see app.main; this ticket store is still
in-process and so this constraint still applies to it.) Making this
horizontally scalable needs a shared store (Redis with a 60s TTL and an atomic
GETDEL, or a signed self-contained ticket plus a shared replay set) —
deliberately NOT done here.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

# Ticket lifetime. Covers mint -> mic permission prompt -> WS handshake.
TICKET_TTL_S = 60

# Hard ceiling on outstanding tickets. Expired entries are swept eagerly, so
# this only bites if a single authenticated user hammers the mint endpoint
# faster than tickets expire; past the cap the oldest are dropped rather than
# letting an authenticated caller grow the process heap without bound.
MAX_OUTSTANDING_TICKETS = 1024


@dataclass(frozen=True)
class VoiceTicket:
    """The identity a redeemed ticket hands to the relay."""

    user_id: str
    user_email: str
    expires_at: float  # monotonic clock, not wall clock (immune to NTP steps)


_tickets: dict[str, VoiceTicket] = {}


def _sweep(now: float) -> None:
    """Drop expired tickets. Called on every mint AND every redeem so the store
    is bounded by live tickets rather than by total tickets ever issued — there
    is no background task here, expiry is driven entirely by traffic."""
    expired = [key for key, t in _tickets.items() if t.expires_at <= now]
    for key in expired:
        _tickets.pop(key, None)


def issue_ticket(user_id: str, user_email: str, *, now: float | None = None) -> tuple[str, int]:
    """Mint a ticket for an already-authenticated user.

    Returns (ticket, expires_in_seconds). `now` is injectable so tests can age a
    ticket without sleeping.
    """
    current = time.monotonic() if now is None else now
    _sweep(current)
    if len(_tickets) >= MAX_OUTSTANDING_TICKETS:
        # Evict the soonest-to-expire first: it is the one closest to being
        # worthless anyway.
        oldest = min(_tickets, key=lambda k: _tickets[k].expires_at)
        _tickets.pop(oldest, None)
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = VoiceTicket(
        user_id=user_id, user_email=user_email, expires_at=current + TICKET_TTL_S
    )
    return ticket, TICKET_TTL_S


def redeem_ticket(ticket: str | None, *, now: float | None = None) -> VoiceTicket | None:
    """Consume a ticket, returning its identity, or None if it is missing,
    unknown, expired or already spent. Redeeming REMOVES it — a second attempt
    with the same string always fails, which is what makes a ticket recovered
    from a log line useless."""
    current = time.monotonic() if now is None else now
    _sweep(current)
    if not ticket:
        return None
    found = _tickets.pop(ticket, None)  # pop, not get: single-use is the point.
    if found is None or found.expires_at <= current:
        return None
    return found


def clear_tickets() -> None:
    """Drop every outstanding ticket. Test helper; also what a graceful restart
    effectively does to this store."""
    _tickets.clear()
