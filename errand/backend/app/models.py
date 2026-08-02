"""ORM models: User, Conversation, Message.

A user owns many conversations; a conversation owns many ordered messages. A
message is either a normal chat turn (role user/assistant) or a structured
record of a tool run (role 'tool') whose `events` column holds the errand audit
timeline as JSON so a saved conversation can re-render the tool cards.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="New chat")
    profile: Mapped[str] = mapped_column(String(16), default="business")
    model: Mapped[str] = mapped_column(String(32), default="sol")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # 'user' | 'assistant' | 'tool'
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    # For role='tool': the errand audit event timeline (list of frames) so the
    # saved conversation can re-render the tool cards exactly.
    events: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class Approval(Base):
    """A single human-in-the-loop spend gate, made durable so the SSE stream that
    AWAITS the gate and the POST /approve that RESOLVES it no longer have to share
    one process. The stream inserts a `pending` row, then polls it; /approve
    flips the row to approved/declined in a separate request (any process). This
    replaces the old in-process `asyncio.Future` rendezvous that pinned the
    approval hand-off to a single worker.

    `scope` is a generic, caller-supplied ownership key — its MEANING and the
    authorization live entirely in the routers: it is the owner's user id on the
    app.main `/api/errand/*` path, and the conversation id on the
    app.routers.chat `/{id}/*` path. The table stays deliberately unaware of what
    a scope is; a resolve is only ever issued under a scope the caller has already
    been proven to own, so a leaked run_id is inert in anyone else's hands.

    The UNIQUE (scope, run_id) constraint makes a gate addressable by exactly that
    pair and stops a second pending row from ever shadowing a run's gate.
    """

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    scope: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(32))
    # 'pending' | 'approved' | 'declined' | 'timeout'
    status: Mapped[str] = mapped_column(String(16), default="pending")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (UniqueConstraint("scope", "run_id", name="uq_approvals_scope_run"),)


Index("ix_messages_conv_created", Message.conversation_id, Message.created_at)

# Hot query: "list this user's conversations, newest activity first"
# (WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?). The single-column
# ix_conversations_user_id can satisfy the WHERE but leaves the ORDER BY as a
# sort over every row the user owns. This composite lets both the filter and the
# ordering be served straight from the index. It is declared ASC on purpose: for
# an equality-pinned leading column the engine can walk the index backwards, so
# ASC serves ORDER BY updated_at DESC just as well as an explicit DESC index
# while staying a plain (portable, reflectable) column index on both SQLite and
# Postgres.
Index("ix_conversations_user_updated", Conversation.user_id, Conversation.updated_at)
