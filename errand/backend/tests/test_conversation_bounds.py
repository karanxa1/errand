"""Pagination bounds on the conversation routes.

Both list endpoints were unbounded, so one user with a long history could pull
every row (and every message's `events` JSON blob) into memory on a page load.
They are now bounded, with the message window taken from the END of the thread
because a chat client wants the newest turns first.

Two properties are easy to break and hard to notice, so both are pinned here:
the caps really reject out-of-range params, and the message window really is the
newest page returned in ascending order (not the oldest page, and not reversed).

Runs under pytest if installed, and standalone (`uv run python
tests/test_conversation_bounds.py`) if not.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import (  # noqa: E402
    api_client,
    create_user,
    ensure_schema,
    register_user,
    run_async,
    run_standalone,
    session_scope,
)

from app.models import Conversation, Message  # noqa: E402
from app.routers.conversations import (  # noqa: E402
    DEFAULT_CONVERSATION_LIMIT,
    DEFAULT_MESSAGE_LIMIT,
    MAX_CONVERSATION_LIMIT,
    MAX_MESSAGE_LIMIT,
)

ensure_schema()

# More rows than a default page, so a missing bound would show up as a longer
# response rather than as an identical one.
SEEDED_CONVERSATIONS = DEFAULT_CONVERSATION_LIMIT + 7
SEEDED_MESSAGES = DEFAULT_MESSAGE_LIMIT * 2 + 20


async def _seed_conversations(user_id: str, count: int) -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with session_scope() as session:
        for i in range(count):
            session.add(
                Conversation(
                    user_id=user_id,
                    title=f"c{i:03d}",
                    created_at=base + timedelta(minutes=i),
                    updated_at=base + timedelta(minutes=i),
                )
            )
        await session.commit()


async def _seed_thread(user_id: str, message_count: int) -> str:
    """A conversation whose messages are strictly ordered in time, so the window
    that comes back identifies itself by content."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with session_scope() as session:
        convo = Conversation(user_id=user_id, title="thread", created_at=base, updated_at=base)
        session.add(convo)
        await session.flush()
        convo_id = convo.id
        for i in range(message_count):
            session.add(
                Message(
                    conversation_id=convo_id,
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"m{i:04d}",
                    created_at=base + timedelta(seconds=i),
                )
            )
        await session.commit()
    return convo_id


def test_list_rejects_out_of_range_pagination() -> None:
    async def scenario() -> None:
        async with api_client() as client:
            _user, headers = await register_user(client)
            for query in (
                "limit=999",
                "limit=0",
                "limit=-1",
                f"limit={MAX_CONVERSATION_LIMIT + 1}",
                "offset=-1",
            ):
                res = await client.get(f"/api/conversations?{query}", headers=headers)
                assert res.status_code == 422, (query, res.status_code, res.text)

            # The edges themselves stay legal.
            for query in ("limit=1", f"limit={MAX_CONVERSATION_LIMIT}", "offset=0"):
                res = await client.get(f"/api/conversations?{query}", headers=headers)
                assert res.status_code == 200, (query, res.text)

    run_async(scenario())


def test_detail_rejects_out_of_range_pagination() -> None:
    async def scenario() -> None:
        async with api_client() as client:
            user, headers = await register_user(client)
            convo_id = await _seed_thread(user["id"], 3)
            for query in (
                "limit=999",
                "limit=0",
                f"limit={MAX_MESSAGE_LIMIT + 1}",
                "offset=-1",
            ):
                res = await client.get(
                    f"/api/conversations/{convo_id}?{query}", headers=headers
                )
                assert res.status_code == 422, (query, res.status_code, res.text)

    run_async(scenario())


def test_list_without_params_returns_the_documented_default_page() -> None:
    """The frontend sends no pagination params, so the no-params response is the
    contract: newest-first, capped at DEFAULT_CONVERSATION_LIMIT."""

    async def scenario() -> None:
        async with api_client() as client:
            user, headers = await register_user(client)
            await _seed_conversations(user["id"], SEEDED_CONVERSATIONS)

            res = await client.get("/api/conversations", headers=headers)
            assert res.status_code == 200, res.text
            rows = res.json()
            assert len(rows) == DEFAULT_CONVERSATION_LIMIT, len(rows)

            # Newest activity first, and it is the newest page, not the oldest.
            titles = [r["title"] for r in rows]
            assert titles[0] == f"c{SEEDED_CONVERSATIONS - 1:03d}", titles[0]
            assert titles == sorted(titles, reverse=True), titles

            # offset pages further back into history.
            res = await client.get(
                f"/api/conversations?limit=5&offset={DEFAULT_CONVERSATION_LIMIT}",
                headers=headers,
            )
            assert res.status_code == 200, res.text
            paged = [r["title"] for r in res.json()]
            assert len(paged) == 5, paged
            first_index = SEEDED_CONVERSATIONS - 1 - DEFAULT_CONVERSATION_LIMIT
            assert paged[0] == f"c{first_index:03d}", paged

    run_async(scenario())


def test_message_window_is_the_newest_page_in_ascending_order() -> None:
    async def scenario() -> None:
        async with api_client() as client:
            user, headers = await register_user(client)
            convo_id = await _seed_thread(user["id"], SEEDED_MESSAGES)

            res = await client.get(f"/api/conversations/{convo_id}", headers=headers)
            assert res.status_code == 200, res.text
            body = res.json()
            contents = [m["content"] for m in body["messages"]]
            assert len(contents) == DEFAULT_MESSAGE_LIMIT, len(contents)

            # The END of the thread: the last DEFAULT_MESSAGE_LIMIT messages...
            expected = [
                f"m{i:04d}"
                for i in range(SEEDED_MESSAGES - DEFAULT_MESSAGE_LIMIT, SEEDED_MESSAGES)
            ]
            assert contents == expected, (contents[0], contents[-1])
            # ...handed back oldest-to-newest, which is what the UI renders.
            stamps = [m["created_at"] for m in body["messages"]]
            assert stamps == sorted(stamps), stamps

            # offset pages BACKWARDS into older history, still ascending.
            res = await client.get(
                f"/api/conversations/{convo_id}?limit=10&offset={DEFAULT_MESSAGE_LIMIT}",
                headers=headers,
            )
            assert res.status_code == 200, res.text
            older = [m["content"] for m in res.json()["messages"]]
            end = SEEDED_MESSAGES - DEFAULT_MESSAGE_LIMIT
            assert older == [f"m{i:04d}" for i in range(end - 10, end)], older

            # An offset past the start of the thread is empty, not an error.
            res = await client.get(
                f"/api/conversations/{convo_id}?offset={SEEDED_MESSAGES + 10}",
                headers=headers,
            )
            assert res.status_code == 200, res.text
            assert res.json()["messages"] == []

    run_async(scenario())


def test_detail_of_someone_elses_conversation_is_404() -> None:
    async def scenario() -> None:
        async with session_scope() as session:
            stranger = await create_user(session)
            stranger_id = stranger.id
        convo_id = await _seed_thread(stranger_id, 3)

        async with api_client() as client:
            _user, headers = await register_user(client)
            res = await client.get(f"/api/conversations/{convo_id}", headers=headers)
            assert res.status_code == 404, res.text
            # Same answer as an id that never existed — no existence oracle.
            missing = await client.get(
                "/api/conversations/" + "0" * 32, headers=headers
            )
            assert missing.status_code == 404
            assert res.json() == missing.json()

    run_async(scenario())


def test_detail_requires_authentication() -> None:
    async def scenario() -> None:
        async with api_client() as client:
            res = await client.get("/api/conversations")
            assert res.status_code in (401, 403), res.text
            res = await client.get("/api/conversations/" + "0" * 32)
            assert res.status_code in (401, 403), res.text

    run_async(scenario())


if __name__ == "__main__":
    sys.exit(1 if run_standalone(dict(globals())) else 0)
