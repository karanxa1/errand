"""Lazy conversation creation: the frontend mints a conversation id locally, puts
it in the URL and starts streaming, so the row is materialized by the first turn
rather than by a blocking POST.

That moves a primary key from the server to the client, which is exactly the sort
of thing that regresses quietly, so this pins the three things that make it safe:
the id must look like a server-generated one, the row must belong to the caller,
and someone else's id must be indistinguishable from one that never existed.

The streaming `chat()` endpoint is NOT exercised here — it calls OpenAI. These
tests drive `_owned_or_created` and the request models directly against a real
SQLite session.

Runs under pytest if installed, and standalone (`uv run python
tests/test_chat_lazy_create.py`) if not.
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import (  # noqa: E402
    create_user,
    ensure_schema,
    run_async,
    run_standalone,
    session_scope,
)

from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.models import Conversation  # noqa: E402
from app.routers.chat import (  # noqa: E402
    _CLIENT_CONVERSATION_ID,
    _owned_or_created,
    ChatRequest,
)

ensure_schema()

# 32 lowercase hex characters, and deliberately containing letters so that
# `.upper()` below is actually a different string.
WELL_FORMED = "0123456789abcdef" * 2

MALFORMED_IDS = [
    WELL_FORMED.upper(),  # uppercase hex — uuid4().hex is lowercase
    WELL_FORMED[:31],  # 31 chars: short ids risk silent truncation collisions
    WELL_FORMED + "a",  # 33 chars
    "../../etc/passwd",  # path traversal
    WELL_FORMED + "\n",  # trailing newline — the pattern uses \Z, not $
    "\n" + WELL_FORMED,
    "",
    "' OR 1=1 --",
    "'; DROP TABLE conversations; --",
    "g" * 32,  # right length, not hex
    " " + WELL_FORMED[1:],
]


def test_client_conversation_id_accepts_uuid4_hex() -> None:
    for _ in range(50):
        cid = uuid.uuid4().hex
        assert _CLIENT_CONVERSATION_ID.match(cid) is not None, cid


def test_client_conversation_id_rejects_anything_else() -> None:
    for bad in MALFORMED_IDS:
        assert _CLIENT_CONVERSATION_ID.match(bad) is None, repr(bad)


def test_first_turn_creates_the_row_owned_by_the_caller() -> None:
    async def scenario() -> None:
        cid = uuid.uuid4().hex
        async with session_scope() as session:
            user = await create_user(session)
            convo = await _owned_or_created(
                session, user, cid, profile="personal", model="luna"
            )
            await session.commit()
            assert convo.id == cid
            assert convo.user_id == user.id
            assert convo.profile == "personal"
            assert convo.model == "luna"
            owner_id = user.id

        # Committed, not just held in the identity map.
        async with session_scope() as session:
            stored = await session.get(Conversation, cid)
            assert stored is not None
            assert stored.user_id == owner_id
            assert stored.profile == "personal"
            assert stored.model == "luna"

    run_async(scenario())


def test_later_turns_return_the_existing_row_without_rewriting_it() -> None:
    """profile/model on the request are only read when the row is born. A later
    turn must not be able to silently re-point a conversation at another model —
    that is what PATCH /api/conversations/{id} is for."""

    async def scenario() -> None:
        cid = uuid.uuid4().hex
        async with session_scope() as session:
            user = await create_user(session)
            first = await _owned_or_created(
                session, user, cid, profile="personal", model="luna"
            )
            await session.commit()
            created_at = first.created_at

            second = await _owned_or_created(
                session, user, cid, profile="business", model="sol"
            )
            await session.commit()
            assert second.id == cid
            assert second.created_at == created_at, "a second row was created"
            assert second.profile == "personal"
            assert second.model == "luna"

        async with session_scope() as session:
            stored = await session.get(Conversation, cid)
            assert stored is not None
            assert stored.profile == "personal"
            assert stored.model == "luna"

    run_async(scenario())


def test_someone_elses_conversation_is_404_not_403() -> None:
    """404 on purpose: 403 would confirm the id exists, turning the endpoint into
    an oracle for enumerating other people's conversation ids."""

    async def scenario() -> None:
        cid = uuid.uuid4().hex
        async with session_scope() as session:
            owner = await create_user(session)
            intruder = await create_user(session)
            await _owned_or_created(
                session, owner, cid, profile="personal", model="luna"
            )
            await session.commit()

            try:
                await _owned_or_created(
                    session, intruder, cid, profile="business", model="sol"
                )
            except HTTPException as exc:
                assert exc.status_code == 404, exc.status_code
                assert exc.status_code != 403
            else:
                raise AssertionError("an intruder was handed someone else's conversation")

            owner_id = owner.id

        # The row is untouched and still the owner's.
        async with session_scope() as session:
            stored = await session.get(Conversation, cid)
            assert stored is not None
            assert stored.user_id == owner_id
            assert stored.profile == "personal"

    run_async(scenario())


def test_malformed_ids_are_404_and_create_nothing() -> None:
    async def scenario() -> None:
        async with session_scope() as session:
            user = await create_user(session)
            user_id = user.id  # read before any rollback expires the instance
            for bad in MALFORMED_IDS:
                try:
                    await _owned_or_created(
                        session, user, bad, profile="business", model="sol"
                    )
                except HTTPException as exc:
                    assert exc.status_code == 404, (bad, exc.status_code)
                else:
                    raise AssertionError(f"{bad!r} was accepted as a conversation id")

            await session.rollback()

        # Nothing was seeded under any of those keys.
        async with session_scope() as session:
            for bad in MALFORMED_IDS:
                assert await session.get(Conversation, bad) is None, repr(bad)
            from sqlalchemy import select

            rows = list(
                await session.scalars(
                    select(Conversation).where(Conversation.user_id == user_id)
                )
            )
            assert rows == [], rows

    run_async(scenario())


def test_chat_request_defaults() -> None:
    req = ChatRequest(content="hello")
    assert req.profile == "business"
    assert req.model == "sol"


def test_chat_request_rejects_an_unknown_model() -> None:
    for bad in ["gpt-4", "SOL", "sol ", "", "opus", "sol,terra"]:
        try:
            ChatRequest(content="hello", model=bad)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"model={bad!r} was accepted")


def test_chat_request_rejects_an_unknown_profile() -> None:
    for bad in ["admin", "Business", "personal ", "", "business;personal"]:
        try:
            ChatRequest(content="hello", profile=bad)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"profile={bad!r} was accepted")


def test_chat_request_bounds_content() -> None:
    ChatRequest(content="x" * 8000)
    for bad in ["", "x" * 8001]:
        try:
            ChatRequest(content=bad)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"content of length {len(bad)} was accepted")


if __name__ == "__main__":
    sys.exit(1 if run_standalone(dict(globals())) else 0)
