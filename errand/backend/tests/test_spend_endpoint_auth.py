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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import (  # noqa: E402
    api_client,
    ensure_schema,
    register_user,
    run_async,
    run_standalone,
)

from app import main as main_module  # noqa: E402
from app.main import ErrandRequest  # noqa: E402

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


def test_errand_request_has_no_identity_fields() -> None:
    """The spender is whoever the token says, so the model must not even carry
    somewhere for a caller-supplied identity to land."""
    fields = set(ErrandRequest.model_fields)
    assert fields == {"profile", "intent", "model"}, fields
    assert "user_id" not in fields
    assert "user_email" not in fields


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


if __name__ == "__main__":
    sys.exit(1 if run_standalone(dict(globals())) else 0)
