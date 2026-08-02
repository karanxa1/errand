#!/usr/bin/env python
"""Exercise the whole Prava MERCHANT API against the live sandbox.

    cd backend && uv run python -m scripts.verify_prava_sandbox

Walks every endpoint the errand path depends on, in order, against
sandbox.api.prava.space with the sk_test_ key, and prints what each one actually
returned. Nothing here can spend money: a sandbox key mints test tokens only,
and no checkout is attempted.

What it does NOT cover, because it cannot: the wallet shop API. There is no
sandbox host for it (sandbox.pay-api / pay-api.sandbox / sandbox.pay do not
resolve), so real-merchant shopping is verifiable only against production with a
linked agent. `scripts/prava_link.py` is the door to that; this script stops at
the sandbox boundary and says so rather than pretending to have checked it.

The one step a machine cannot do is card entry: `payment-result` stays `pending`
until a human opens the iframe URL and types a test card. So the poll below is a
SHAPE check (the endpoint answers, with the documented status vocabulary), not a
wait for a credential. Pass --wait to hold the session open and poll for real
after you have opened the URL.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.brokers.prava import PravaApiError, PravaPaymentBroker  # noqa: E402
from app.config import settings  # noqa: E402
from app.contracts import (  # noqa: E402
    CartItem,
    CreateSessionInput,
    Merchant,
    PollCompleted,
    PollFailed,
    PollPending,
)

OK = "  ok  "
BAD = " FAIL "
SKIP = " skip "


def line(mark: str, text: str) -> None:
    print(f"[{mark}] {text}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--wait",
        type=int,
        default=0,
        metavar="SECONDS",
        help="After printing the iframe URL, poll for a credential this long.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Do not revoke the session at the end (leave it open to play with).",
    )
    args = parser.parse_args()
    failures = 0

    print("=== configuration ===")
    if not settings.prava_secret_key:
        line(BAD, "PRAVA_SECRET_KEY is not set — nothing to verify.")
        return 1
    line(OK, f"api base           {settings.prava_api_base}")
    line(OK, f"secret key         {settings.prava_secret_key[:8]}… (sandbox={settings.prava_is_sandbox})")
    problem = settings.prava_key_environment_problem
    if problem:
        line(BAD, problem)
        failures += 1
    else:
        line(OK, "key and host environments agree")
    if not settings.prava_is_sandbox:
        line(
            BAD,
            "this is NOT the sandbox — refusing to run a live-key walkthrough that "
            "creates real sessions. Point PRAVA_API_BASE at sandbox to use this script.",
        )
        return 1

    broker = PravaPaymentBroker(
        settings.prava_secret_key,
        settings.prava_api_base,
        callback_url=settings.prava_callback_url,
        user_country=settings.prava_user_country,
        merchant_category_code=settings.prava_merchant_category_code,
        merchant_category=settings.prava_merchant_category,
    )

    print("\n=== GET /health ===")
    if await broker.health():
        line(OK, "backend is up")
    else:
        line(BAD, "health check failed")
        failures += 1

    print("\n=== POST /v1/sessions ===")
    user_id = f"u_verify_{int(time.time())}"
    try:
        session = await broker.create_session(
            CreateSessionInput(
                merchant=Merchant(name="Demo Pantry Co", url="https://demo-pantry.example.com"),
                total_cents=6300,
                user_id=user_id,
                user_email="agent@demo.agentmail.to",
                items=[
                    CartItem(name="Dark roast beans 1kg", qty=1, price_cents=4200),
                    CartItem(name="Sparkling water 24x330ml", qty=1, price_cents=2100),
                ],
                external_order_ref=f"errand-verify-{user_id}",
            )
        )
    except PravaApiError as exc:
        line(BAD, f"session creation failed — {exc}")
        return failures + 1
    line(OK, f"session_id         {session.session_id}")
    line(OK, f"order_id           {session.order_id or '(none returned)'}")
    line(OK, f"expires_at         {session.expires_at or '(none returned)'}")
    line(
        OK if session.session_token else SKIP,
        f"session_token      {'present (' + str(len(session.session_token)) + ' chars)' if session.session_token else 'not returned'}",
    )
    line(OK, f"iframe_url         {session.iframe_url[:80]}…")

    print("\n=== GET /v1/sessions/{id}/payment-result ===")
    result = await broker.poll_credential(session.session_id)
    if isinstance(result, PollPending):
        line(OK, "pending — as expected before a human enters a card")
    elif isinstance(result, PollCompleted):
        line(OK, f"credential issued, token ends {result.credential.token[-4:]}")
    else:
        line(BAD, f"unexpected failure: {result.code} {result.message}")
        failures += 1

    print("\n=== GET /v1/listCards ===")
    try:
        cards = await broker.list_cards(user_id)
        line(OK, f"{len(cards)} card(s) on file for a brand-new customer id")
    except PravaApiError as exc:
        line(BAD, f"listCards failed — {exc}")
        failures += 1

    credential = None
    if args.wait:
        print(f"\n=== waiting up to {args.wait}s for card entry ===")
        print(f"    Open: {session.iframe_url}\n")
        deadline = time.monotonic() + args.wait
        while time.monotonic() < deadline:
            result = await broker.poll_credential(session.session_id)
            if isinstance(result, PollCompleted):
                credential = result.credential
                line(OK, f"credential issued — token ends {credential.token[-4:]}, "
                         f"cvv {len(credential.dynamic_cvv)} digits, "
                         f"exp {credential.expiry_month}/{credential.expiry_year}")
                break
            if isinstance(result, PollFailed):
                line(BAD, f"payment failed: {result.code} {result.message}")
                failures += 1
                break
            print(".", end="", flush=True)
            await asyncio.sleep(3)
        else:
            print()
            line(SKIP, "no card entered in time")

    if credential is not None:
        print("\n=== POST /v1/sessions/{id}/report-status ===")
        try:
            # Honest report: this script never presented the card at a merchant,
            # so the outcome is DECLINED. Reporting APPROVED here would tell Visa
            # a purchase happened that did not.
            await broker.report_status(
                session.session_id, credential.txn_ref_id, "DECLINED"
            )
            line(OK, "reported DECLINED (nothing was actually charged)")
        except PravaApiError as exc:
            line(BAD, f"report-status failed — {exc}")
            failures += 1

    if not args.keep and credential is None:
        print("\n=== POST /v1/sessions/{id}/revoke ===")
        try:
            await broker.revoke_session(session.session_id)
            line(OK, "session revoked")
        except PravaApiError as exc:
            line(SKIP, f"revoke unavailable — {exc}")

    print("\n=== wallet shop API (production only) ===")
    line(SKIP, "no sandbox host exists; run scripts/prava_link.py to test in production")

    print()
    print("PASS" if failures == 0 else f"FAIL — {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
