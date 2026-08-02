"""Shared bootstrap for the backend test suite.

Two things have to happen BEFORE anything under `app.` is imported, because
`app.config.settings` and `app.db.engine` are module-level singletons built at
import time. That is why the environment below is set as top-level code rather
than inside a fixture:

  1. Point the process at a throwaway SQLite file, so no test can ever touch the
     developer's ./errand.db — or, far worse, a real DATABASE_URL.
  2. Supply a JWT secret that satisfies `Settings.jwt_secret_problem`, so tokens
     minted in a test verify exactly the way they would in a deployment instead
     of riding the published dev default.

Everything else here is plain helper functions rather than pytest fixtures, so
each test module stays runnable standalone (`uv run python tests/test_x.py`)
just like test_voice_tickets.py, which is the house style in this directory.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import tempfile
import uuid

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))

os.environ["ENVIRONMENT"] = "dev"
# Long enough to clear MIN_JWT_SECRET_LEN and not the in-repo dev default, so
# the startup guard is satisfied rather than merely warned past.
os.environ["JWT_SECRET"] = "errand-tests-" + ("0123456789abcdef" * 3)
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="errand-tests-"), "test.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

# The suite has no pytest-asyncio dependency, so async work is driven manually.
# ONE shared loop for the whole session: the ASGI client and any direct session
# work then run on the same loop, which keeps SQLAlchemy's pooled aiosqlite
# connections on the loop that created them.
_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)

# A short ASCII password: bcrypt hard-rejects secrets over 72 BYTES.
TEST_PASSWORD = "pw-test-1234"


def run_async(coro):
    """Run a coroutine on the suite's shared event loop."""
    return _LOOP.run_until_complete(coro)


_schema_ready = False


def ensure_schema() -> None:
    """Create the ORM tables in the throwaway database (idempotent)."""
    global _schema_ready
    if _schema_ready:
        return
    from app.db import init_db

    run_async(init_db())
    _schema_ready = True


def session_scope():
    """An `async with`-able AsyncSession against the throwaway database."""
    from app.db import SessionLocal

    return SessionLocal()


@contextlib.asynccontextmanager
async def api_client():
    """An httpx client speaking ASGI straight to the app, on the shared loop.

    Deliberately not starlette's TestClient: that runs the app on its own loop
    in a portal thread, which would put app requests and the direct-session
    tests on two different loops over one SQLAlchemy engine.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


async def register_user(client, *, name: str = "Test User") -> tuple[dict, dict]:
    """Register a real user over HTTP and return (user_json, auth_headers).

    A genuine round trip rather than a hand-forged JWT, so the token under test
    is one the app itself issued.
    """
    email = unique_email()
    res = await client.post(
        "/api/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "name": name},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    return body["user"], {"Authorization": f"Bearer {body['token']}"}


async def create_user(session, *, email: str | None = None):
    """Insert a user directly, for tests that exercise helpers below the HTTP
    layer and only need an owner to hang rows off."""
    from app.auth import hash_password
    from app.models import User

    user = User(
        email=email or unique_email(),
        name="",
        password_hash=hash_password(TEST_PASSWORD),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def run_standalone(namespace: dict) -> int:
    """Run every test_* callable in `namespace`; return the failure count.

    Mirrors the `__main__` blocks in test_voice_tickets.py / test_voice_ws_auth.py
    so these modules work with or without a test runner installed.
    """
    failures = 0
    for name, fn in sorted(namespace.items()):
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
    return failures
