"""The two endpoints that can move real money must never be reachable
anonymously, and must never take the spender's identity from the request body.

`POST /api/errand/stream` drives the real purchasing orchestrator (Prava payment
session + checkout) and `POST /api/errand/{run_id}/approve` resolves the spend
approval gate. Both were unauthenticated until today.

SAFETY: no test here is allowed to let `run_errand` actually run — it buys
things. The one test that goes past the auth layer stubs BOTH `run_errand` and
`build_brokers` first and asserts the stub was the thing that ran, so nothing
outbound can fire.

Runs under pytest if installed, and standalone (`uv run python
tests/test_spend_endpoint_auth.py`) if not.
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import (  # noqa: E402
    api_client,
    ensure_schema,
    register_user,
    run_async,
    run_standalone,
    session_scope,
)

from app import main as main_module  # noqa: E402
from app.main import ErrandRequest  # noqa: E402
from app.models import Approval  # noqa: E402
from sqlalchemy import select  # noqa: E402

ensure_schema()

# A body that is valid on its own terms, so a rejection can only be about auth.
VALID_BODY = {
    "profile": "business",
    "intent": "test intent — must never reach a broker",
    "model": "sol",
}

UNAUTHORIZED = (401, 403)


def test_stream_rejects_an_anonymous_caller() -> None:
    async def scenario() -> None:
        async with api_client() as client:
            res = await client.post("/api/errand/stream", json=VALID_BODY)
            assert res.status_code in UNAUTHORIZED, res.text
            res = await client.post(
                "/api/errand/stream",
                json=VALID_BODY,
                headers={"Authorization": "Bearer not-a-real-token"},
            )
            assert res.status_code in UNAUTHORIZED, res.text
            # An empty bearer must not be treated as "no auth needed".
            res = await client.post(
                "/api/errand/stream",
                json=VALID_BODY,
                headers={"Authorization": "Bearer "},
            )
            assert res.status_code in UNAUTHORIZED, res.text

    run_async(scenario())


def test_approve_rejects_an_anonymous_caller() -> None:
    """Knowing (or guessing) a run_id must not be enough to approve a purchase."""

    async def scenario() -> None:
        async with api_client() as client:
            body = {"approved": True}
            res = await client.post("/api/errand/run-does-not-exist/approve", json=body)
            assert res.status_code in UNAUTHORIZED, res.text
            res = await client.post(
                "/api/errand/run-does-not-exist/approve",
                json=body,
                headers={"Authorization": "Bearer not-a-real-token"},
            )
            assert res.status_code in UNAUTHORIZED, res.text

    run_async(scenario())


# Fields the request model is ALLOWED to carry. An exact-set assertion, not a
# subset one: the point is that adding a field to a spend endpoint has to be a
# deliberate act that fails this test first and makes someone justify it.
#
# `browser_profile_id` was justified as follows. It identifies the DEVICE, not
# the spender: it is a random opaque id the browser mints once and persists, and
# the server cannot derive it. Forwarded to Prava, it is what stops every
# checkout looking like a brand-new device — which would force a fresh passkey
# registration each time and burn one of a hard-capped number of token bindings
# until the card is permanently unusable. Crucially, it is not a capability:
# nothing is authorised by it, and supplying someone else's value attributes no
# purchase to them, because the spender still comes from the bearer token.
_ALLOWED_REQUEST_FIELDS = {"profile", "intent", "model", "browser_profile_id"}


def test_errand_request_has_no_identity_fields() -> None:
    """The spender is whoever the token says, so the model must not even carry
    somewhere for a caller-supplied identity to land."""
    fields = set(ErrandRequest.model_fields)
    assert fields == _ALLOWED_REQUEST_FIELDS, fields
    assert "user_id" not in fields
    assert "user_email" not in fields
    # The device id must never become a way to say WHO is spending.
    assert not any(
        f in fields for f in ("user", "email", "customer_id", "account_id", "sub")
    ), fields


def test_errand_request_drops_identity_smuggled_in_the_body() -> None:
    req = ErrandRequest(
        **{
            "profile": "business",
            "intent": "x",
            "model": "sol",
            "user_id": "u_attacker",
            "user_email": "attacker@evil.example",
        }
    )
    assert not hasattr(req, "user_id")
    assert not hasattr(req, "user_email")
    dumped = req.model_dump()
    assert "user_id" not in dumped, dumped
    assert "user_email" not in dumped, dumped
    assert "attacker" not in repr(dumped)


def test_valid_token_passes_auth_and_the_identity_comes_from_the_token() -> None:
    """The positive half: a real token is accepted (no 401), and the identity
    handed to the orchestrator is derived from that token — not from the
    user_id / user_email an attacker stuffed into the body.

    `run_errand` and `build_brokers` are replaced for the duration, so the
    request stops at the stub and nothing outbound is ever dialled.
    """
    calls: list[dict] = []
    built: list[str] = []

    class _StubBrokers:
        """Not the real Brokers object: touching any broker is a test failure."""

        def __getattr__(self, item: str):  # pragma: no cover - defensive
            raise AssertionError(f"a broker ({item}) was used; this test must not spend")

    async def _stub_run_errand(brokers, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return {"ok": True, "stubbed": True}

    def _stub_build_brokers():  # type: ignore[no-untyped-def]
        built.append("brokers")
        return _StubBrokers()

    original_run_errand = main_module.run_errand
    original_build_brokers = main_module.build_brokers
    main_module.run_errand = _stub_run_errand  # type: ignore[assignment]
    main_module.build_brokers = _stub_build_brokers  # type: ignore[assignment]

    async def scenario() -> None:
        async with api_client() as client:
            user, headers = await register_user(client)
            res = await client.post(
                "/api/errand/stream",
                json={
                    **VALID_BODY,
                    # Smuggled identity: must be ignored end to end.
                    "user_id": "u_attacker",
                    "user_email": "attacker@evil.example",
                },
                headers=headers,
            )
            assert res.status_code == 200, res.text
            assert "run.started" in res.text
            assert "run.done" in res.text

        assert built == ["brokers"]
        assert len(calls) == 1, calls
        kwargs = calls[0]
        # main.py derives this as f"u_{user.id[:12]}" from the verified token.
        assert kwargs["user_id"] == f"u_{user['id'][:12]}", kwargs["user_id"]
        assert kwargs["user_email_fallback"] == user["email"]
        assert "attacker" not in kwargs["user_id"]
        assert "attacker" not in kwargs["user_email_fallback"]

    try:
        run_async(scenario())
    finally:
        main_module.run_errand = original_run_errand  # type: ignore[assignment]
        main_module.build_brokers = original_build_brokers  # type: ignore[assignment]


def test_valid_token_passes_auth_on_approve() -> None:
    """An authenticated approve for a run that does not exist is answered by the
    handler (ok: false), not by the auth layer — proving the token got through
    without resolving any real gate."""

    async def scenario() -> None:
        async with api_client() as client:
            _user, headers = await register_user(client)
            res = await client.post(
                "/api/errand/no-such-run/approve",
                json={"approved": True},
                headers=headers,
            )
            assert res.status_code == 200, res.text
            assert res.json() == {
                "ok": False,
                "reason": "no pending approval for this run",
            }, res.text

    run_async(scenario())


async def _approval_status(scope: str, run_id: str) -> str | None:
    """The stored status of a gate row, or None if it does not exist."""
    async with session_scope() as s:
        row = (
            await s.scalars(
                select(Approval).where(
                    Approval.scope == scope, Approval.run_id == run_id
                )
            )
        ).first()
        return row.status if row is not None else None


def test_approve_is_scoped_to_the_run_owner() -> None:
    """A stranger with a valid token must not be able to resolve someone else's
    spend gate, and the owner must still be able to resolve their own.

    Authenticating /approve is necessary but not sufficient — the gate row is
    scoped by (user_id, run_id) and /approve UPDATEs WHERE scope=caller.id, so a
    leaked run_id is inert in anyone else's hands. Both halves are asserted here:
    without the second one, scoping the gate to nobody at all would also pass.

    No errand is started: a pending row is seeded directly exactly the way
    errand_stream's DB-backed gate would, so nothing outbound can fire either way.
    """

    async def scenario() -> None:
        async with api_client() as client:
            owner, owner_headers = await register_user(client)
            _intruder, intruder_headers = await register_user(client)

            run_id = uuid.uuid4().hex
            # Seed the gate exactly as errand_stream's approve() path does: a
            # pending row scoped to the owner. No run, no broker, no spend.
            async with session_scope() as s:
                s.add(Approval(scope=owner["id"], run_id=run_id, status="pending"))
                await s.commit()
            try:
                res = await client.post(
                    f"/api/errand/{run_id}/approve",
                    json={"approved": True},
                    headers=intruder_headers,
                )
                assert await _approval_status(owner["id"], run_id) == "pending", (
                    "a stranger resolved someone else's spend approval gate"
                )
                assert res.json().get("ok") is not True, res.text
                # Indistinguishable from a run that never existed, so this cannot
                # be used to probe which run ids are live.
                assert res.json() == {
                    "ok": False,
                    "reason": "no pending approval for this run",
                }, res.text

                res = await client.post(
                    f"/api/errand/{run_id}/approve",
                    json={"approved": True},
                    headers=owner_headers,
                )
                assert res.json() == {"ok": True, "approved": True}, res.text
                assert await _approval_status(owner["id"], run_id) == "approved", (
                    "the run's owner could not resolve their own gate"
                )
            finally:
                await main_module._delete_approval(owner["id"], run_id)

    run_async(scenario())


if __name__ == "__main__":
    sys.exit(1 if run_standalone(dict(globals())) else 0)
