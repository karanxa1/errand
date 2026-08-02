"""Database engine + session + ORM base.

One async SQLAlchemy engine, shared models. Local dev uses SQLite (aiosqlite);
production sets DATABASE_URL to Postgres (asyncpg). `init_db` creates tables on
startup for SQLite dev; in prod, Alembic migrations own the schema (init_db is a
safe no-op there because the tables already exist).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# `check_same_thread` is a SQLite-only arg; only pass connect_args for SQLite.
_is_sqlite = settings.sqlalchemy_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine = create_async_engine(
    settings.sqlalchemy_url,
    echo=False,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session, always closed after the request."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables if missing. Used for SQLite dev; harmless in prod where
    Alembic already created them (create_all only adds what's absent)."""
    # Import models so they register on Base.metadata before create_all.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
