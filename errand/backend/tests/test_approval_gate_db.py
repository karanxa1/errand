"""The DB-backed approval-gate rendezvous.

The human-in-the-loop spend gate used to be an in-process `asyncio.Future`: the
SSE stream awaited it and POST /approve resolved it, which only worked while both
requests hit the same process. It is now a row in the `approvals` table — the
stream INSERTs a `pending` row and POLLS it, and /approve UPDATEs it in a
separate request — so the hand-off survives the two landing on different workers.

This pins the properties that make that safe and correct:
  (a) reaching the gate creates a `pending` row,
  (b) the OWNING scope can resolve it and the poll observes the resolution,
  (c) a DIFFERENT scope/user CANNOT resolve it (the security property — a leaked
      run_id is inert in anyone else's hands),
  (d) the wall-clock timeout emits `approval.timeout` and returns timed_out,
  (e) teardown deletes the row so the table can't accumulate.

SAFETY: no test here starts a real errand. `run_errand` / `build_brokers` are
never called — the gate is exercised directly (the awaiting helper and the
/approve endpoints), with pending rows created the same way `run_errand` would,
so nothing outbound can ever fire.

Runs under pytest if installed, and standalone (`uv run python
tests/test_approval_gate_db.py`) if not.
"""

from __future__ import annotations

import asyncio
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

from sqlalchemy import select  # noqa: E402

from app import main as main_module  # noqa: E402
from app.models import Approval, Conversation  # noqa: E402
from app.orchestrator.stream import EventStream  # noqa: E402

ensure_schema()


async def _get_approval(scope: str, run_id: str):
    """Read a gate row (or None), materializing its fields before the session
    closes so callers can inspect a detached instance safely."""
    async with session_scope() as s:
        row = (
            await s.scalars(
                select(Approval).where(Approval.scope == scope, Approval.run_id == run_id)
            )
        ).first()
        if row is not None:
            # Touch the columns while the session is live.
            _ = (row.status, row.reason, row.resolved_at)
        return row


async def _insert_pending(scope: str, run_id: str) -> None:
    """Park a pending gate exactly the way the stream's approve() wrapper does,
    without starting an errand — so nothing outbound can fire."""
    async with session_scope() as s:
        s.add(Approval(scope=scope, run_id=run_id, status="pending"))
        await s.commit()


def test_pending_row_is_created_then_resolution_is_observed() -> None:
    """(a)+(b): the awaiting helper inserts a `pending` row, and once the OWNER
    resolves it over the real /approve endpoint, the poll returns the decision.

    The whole round trip goes through app.main's DB helper and the HTTP endpoint,
    so it proves the two sides rendezvous purely through the table."""

    async def scenario() -> None:
        # Snappier poll so the test doesn't wait a full second for the observe.
        original_interval = main_module._APPROVAL_POLL_INTERVAL_S
        main_module._APPROVAL_POLL_INTERVAL_S = 0.02
        try:
            async with api_client() as client:
                owner, owner_headers = await register_user(client)
                scope = owner["id"]
                run_id = uuid.uuid4().hex
                approval_id = uuid.uuid4().hex
                stream = EventStream()
                cancel = asyncio.Event()

                task = asyncio.create_task(
                    main_module._await_approval_via_db(
                        scope=scope,
                        run_id=run_id,
                        approval_id=approval_id,
                        stream=stream,
                        request_payload={"cart": "…"},
                        cancel=cancel,
                    )
                )

                # (a) the pending row shows up.
                row = None
                for _ in range(200):
                    row = await _get_approval(scope, run_id)
                    if row is not None:
                        break
                    await asyncio.sleep(0.01)
                assert row is not None, "no pending approval row was created"
                assert row.status == "pending", row.status
                assert row.resolved_at is None

                # (b) the owner resolves it over the real endpoint…
                res = await client.post(
                    f"/api/errand/{run_id}/approve",
                    json={"approved": True},
                    headers=owner_headers,
                )
                assert res.json() == {"ok": True, "approved": True}, res.text

                # …and the awaiting poll observes the resolution.
                decision = await asyncio.wait_for(task, timeout=10)
                assert decision.approved is True
                assert decision.approval_id == approval_id
                assert decision.timed_out is False
        finally:
            main_module._APPROVAL_POLL_INTERVAL_S = original_interval

    run_async(scenario())


def test_decline_with_reason_is_carried_through() -> None:
    """A typed decline flows back as approved=False with the operator's reason."""

    async def scenario() -> None:
        original_interval = main_module._APPROVAL_POLL_INTERVAL_S
        main_module._APPROVAL_POLL_INTERVAL_S = 0.02
        try:
            async with api_client() as client:
                owner, owner_headers = await register_user(client)
                scope = owner["id"]
                run_id = uuid.uuid4().hex
                approval_id = uuid.uuid4().hex
                stream = EventStream()
                cancel = asyncio.Event()

                task = asyncio.create_task(
                    main_module._await_approval_via_db(
                        scope=scope,
                        run_id=run_id,
                        approval_id=approval_id,
                        stream=stream,
                        request_payload={},
                        cancel=cancel,
                    )
                )
                for _ in range(200):
                    if await _get_approval(scope, run_id) is not None:
                        break
                    await asyncio.sleep(0.01)

                res = await client.post(
                    f"/api/errand/{run_id}/approve",
                    json={"approved": False, "reason": "over budget"},
                    headers=owner_headers,
                )
                assert res.json() == {"ok": True, "approved": False}, res.text

                decision = await asyncio.wait_for(task, timeout=10)
                assert decision.approved is False
                assert decision.reason == "over budget"
                assert decision.timed_out is False
        finally:
            main_module._APPROVAL_POLL_INTERVAL_S = original_interval

    run_async(scenario())


def test_a_different_user_cannot_resolve_someone_elses_gate() -> None:
    """(c): the security property, DB-backed. A stranger with a valid token must
    not be able to resolve someone else's spend gate, and the owner still can.

    Keyed by scope=user.id, an /approve from anyone else matches zero rows and
    resolves nothing — and its response is byte-identical to a run that never
    existed, so it cannot be used to probe which run ids are live. Without the
    positive half, scoping the gate to nobody at all would also pass, so both are
    asserted. No errand is started; a bare pending row is parked directly."""

    async def scenario() -> None:
        async with api_client() as client:
            owner, owner_headers = await register_user(client)
            _intruder, intruder_headers = await register_user(client)

            run_id = uuid.uuid4().hex
            await _insert_pending(owner["id"], run_id)

            # The intruder cannot resolve it, and cannot tell it apart from a
            # nonexistent run.
            res = await client.post(
                f"/api/errand/{run_id}/approve",
                json={"approved": True},
                headers=intruder_headers,
            )
            assert res.json() == {
                "ok": False,
                "reason": "no pending approval for this run",
            }, res.text
            still = await _get_approval(owner["id"], run_id)
            assert still is not None and still.status == "pending", (
                "a stranger resolved someone else's spend approval gate"
            )

            # The owner can.
            res = await client.post(
                f"/api/errand/{run_id}/approve",
                json={"approved": True},
                headers=owner_headers,
            )
            assert res.json() == {"ok": True, "approved": True}, res.text
            resolved = await _get_approval(owner["id"], run_id)
            assert resolved is not None and resolved.status == "approved"
            assert resolved.resolved_at is not None

    run_async(scenario())


def test_chat_approve_requires_conversation_ownership() -> None:
    """(c) on the chat path: /{id}/approve calls _owned() FIRST, so an intruder
    hitting a conversation they don't own is a 404 — they never reach the UPDATE
    — while the owner resolves the gate. The 404-before-UPDATE ordering is the
    authorization for this path (scope is the conversation id)."""

    async def scenario() -> None:
        async with api_client() as client:
            owner, owner_headers = await register_user(client)
            # A conversation genuinely owned by `owner`, plus a pending gate for
            # it — parked directly, so no errand runs.
            async with session_scope() as session:
                convo = Conversation(
                    id=uuid.uuid4().hex,
                    user_id=owner["id"],
                    profile="business",
                    model="sol",
                )
                session.add(convo)
                await session.commit()
                convo_id = convo.id

            run_id = uuid.uuid4().hex
            await _insert_pending(convo_id, run_id)

            # An intruder (separate real account) is a 404 — indistinguishable
            # from a conversation that doesn't exist — and never reaches the
            # UPDATE, so the gate is untouched.
            _intruder, intruder_headers = await register_user(client)
            res = await client.post(
                f"/api/conversations/{convo_id}/approve",
                json={"run_id": run_id, "approved": True},
                headers=intruder_headers,
            )
            assert res.status_code == 404, res.text
            still = await _get_approval(convo_id, run_id)
            assert still is not None and still.status == "pending", (
                "an intruder resolved a gate in a conversation they don't own"
            )

            # The owner resolves their own gate.
            res = await client.post(
                f"/api/conversations/{convo_id}/approve",
                json={"run_id": run_id, "approved": True},
                headers=owner_headers,
            )
            assert res.json() == {"ok": True, "approved": True}, res.text
            resolved = await _get_approval(convo_id, run_id)
            assert resolved is not None and resolved.status == "approved"

    run_async(scenario())


def test_timeout_path_emits_and_returns_timed_out() -> None:
    """(d): with no resolution before APPROVAL_TIMEOUT_S, the helper emits
    `approval.timeout` and returns a timed_out decision (spend aborts)."""

    async def scenario() -> None:
        original_timeout = main_module.APPROVAL_TIMEOUT_S
        original_interval = main_module._APPROVAL_POLL_INTERVAL_S
        main_module.APPROVAL_TIMEOUT_S = 0.05
        main_module._APPROVAL_POLL_INTERVAL_S = 0.01
        try:
            scope = "scope-" + uuid.uuid4().hex[:8]
            run_id = uuid.uuid4().hex
            approval_id = uuid.uuid4().hex
            stream = EventStream()
            cancel = asyncio.Event()

            frames: list[tuple[str, dict]] = []

            async def collect() -> None:
                async for frame in stream.drain():
                    frames.append(frame)

            drainer = asyncio.create_task(collect())
            decision = await asyncio.wait_for(
                main_module._await_approval_via_db(
                    scope=scope,
                    run_id=run_id,
                    approval_id=approval_id,
                    stream=stream,
                    request_payload={},
                    cancel=cancel,
                ),
                timeout=10,
            )
            await stream.close()
            await asyncio.wait_for(drainer, timeout=5)

            assert decision.approved is False
            assert decision.timed_out is True
            assert decision.approval_id == approval_id
            joined = "".join(frames)
            assert "approval.request" in joined
            assert "approval.timeout" in joined
        finally:
            main_module.APPROVAL_TIMEOUT_S = original_timeout
            main_module._APPROVAL_POLL_INTERVAL_S = original_interval

    run_async(scenario())


def test_cancel_unblocks_the_poll() -> None:
    """Client-disconnect: when the cancel token fires (body() teardown), the poll
    returns a declined 'stream closed' decision instead of hanging to timeout."""

    async def scenario() -> None:
        original_interval = main_module._APPROVAL_POLL_INTERVAL_S
        main_module._APPROVAL_POLL_INTERVAL_S = 0.02
        try:
            scope = "scope-" + uuid.uuid4().hex[:8]
            run_id = uuid.uuid4().hex
            approval_id = uuid.uuid4().hex
            stream = EventStream()
            cancel = asyncio.Event()

            task = asyncio.create_task(
                main_module._await_approval_via_db(
                    scope=scope,
                    run_id=run_id,
                    approval_id=approval_id,
                    stream=stream,
                    request_payload={},
                    cancel=cancel,
                )
            )
            for _ in range(200):
                if await _get_approval(scope, run_id) is not None:
                    break
                await asyncio.sleep(0.01)
            cancel.set()
            decision = await asyncio.wait_for(task, timeout=10)
            assert decision.approved is False
            assert decision.timed_out is False
            assert decision.reason == "stream closed"
        finally:
            main_module._APPROVAL_POLL_INTERVAL_S = original_interval

    run_async(scenario())


def test_teardown_deletes_the_row() -> None:
    """(e): _delete_approval drops the run's row so the table can't accumulate,
    and a late /approve afterwards then correctly finds nothing."""

    async def scenario() -> None:
        async with api_client() as client:
            owner, owner_headers = await register_user(client)
            scope = owner["id"]
            run_id = uuid.uuid4().hex
            await _insert_pending(scope, run_id)
            assert await _get_approval(scope, run_id) is not None

            await main_module._delete_approval(scope, run_id)
            assert await _get_approval(scope, run_id) is None

            # A late approve for the torn-down run is ok:false, not a crash.
            res = await client.post(
                f"/api/errand/{run_id}/approve",
                json={"approved": True},
                headers=owner_headers,
            )
            assert res.json() == {
                "ok": False,
                "reason": "no pending approval for this run",
            }, res.text

    run_async(scenario())


if __name__ == "__main__":
    sys.exit(1 if run_standalone(dict(globals())) else 0)
