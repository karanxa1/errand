"""ORM models: User, Conversation, Message, Approval, McpServer, McpOAuthSession.

A user owns many conversations; a conversation owns many ordered messages. A
message is either a normal chat turn (role user/assistant) or a structured
record of a tool run (role 'tool') whose `events` column holds the errand audit
timeline as JSON so a saved conversation can re-render the tool cards.

A user also owns many MCP servers (their own custom tool providers), each of
which may own OAuth sessions holding the credentials for that server.
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
    mcp_servers: Mapped[list["McpServer"]] = relationship(
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


class McpServer(Base):
    """One user-registered MCP server: a custom tool provider for their agent.

    OWNERSHIP IS THE AUTHORIZATION, and it is per-user rather than global. Every
    read and write goes through a helper that filters on `user_id`, and a row
    belonging to someone else is reported as 404 rather than 403 — the same rule
    the conversation routes follow, for the same reason: a caller must not be
    able to probe which server ids exist. This matters more here than for a
    conversation, because a server row carries credentials and because its tools
    are handed to an LLM that can also spend money.

    `config` is the transport description, shape-discriminated the way
    better-chatbot does it (a `url` key means remote, a `command` key means
    stdio) rather than by a separate type column, so a config is self-describing
    and cannot disagree with its own label. See app/mcp/config.py.

    `auth_mode` is which of the three credential styles this server uses:
      'none'    — an open server, nothing to send.
      'headers' — static secrets (a bearer token, an API key) that we send on
                  every request. Held in `secret_headers`, ENCRYPTED AT REST.
      'oauth'   — OAuth 2.1 + PKCE. Credentials live in McpOAuthSession, also
                  encrypted; this column only records the intent.

    `tools_json` is the tool catalogue as last seen, cached deliberately: the
    chat and voice paths need the tool LIST on every single turn, and paying a
    connect + initialize + tools/list round trip per turn (per server) would put
    seconds of network on the critical path before the model even starts. So the
    hot path reads this column and only an actual tool INVOCATION opens a
    connection. The cache is refreshed whenever we do connect. Cribbed from
    better-chatbot's `toolInfo` column, which exists for the same reason.

    `enabled` is the user's on/off switch: a disabled server keeps its row and
    its credentials but contributes no tools, so turning a noisy or broken
    provider off never means re-authorizing it later.
    """

    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Display name, and the human half of every namespaced tool id.
    name: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JSON)
    # 'none' | 'headers' | 'oauth'
    auth_mode: Mapped[str] = mapped_column(String(16), default="none")
    # Encrypted blob (app/mcp/crypto.py), never the raw header map. Null when
    # auth_mode != 'headers'.
    secret_headers: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    # Last observed catalogue: [{name, description, input_schema}, ...]
    tools_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tools_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 'unknown' | 'connected' | 'authorizing' | 'error' — what the last connect
    # attempt concluded, so the UI can show real state without reconnecting.
    last_status: Mapped[str] = mapped_column(String(16), default="unknown")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    user: Mapped["User"] = relationship(back_populates="mcp_servers")
    oauth_sessions: Mapped[list["McpOAuthSession"]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )

    # A name is the human half of a namespaced tool id, so two servers sharing
    # one name under the same user would produce colliding tool ids and the model
    # would have no way to say which it meant. Unique PER USER, not globally:
    # two different people may both call their server "github".
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_mcp_servers_user_name"),
    )


class McpOAuthSession(Base):
    """OAuth credentials for one MCP server, so consent survives a restart.

    The MCP Python SDK asks for a `TokenStorage` with exactly two pairs of
    getters/setters — the token set, and the dynamically-registered client
    record. Both are persisted here (app/mcp/storage.py is the adapter), because
    the alternative, an in-memory store, would silently re-run the whole consent
    flow on every deploy: the user would be asked to reauthorize a server they
    already authorized, and the server would accumulate a fresh dynamic client
    registration each time.

    BOTH COLUMNS ARE ENCRYPTED AT REST (app/mcp/crypto.py). `tokens` holds a live
    access token — and often a refresh token, which is a long-lived credential to
    a third-party account. `client_info` can hold an issued `client_secret`.
    Neither is something to keep as readable JSON in a shared database.

    `state` is the OAuth CSRF value for one in-flight attempt and is UNIQUE: it
    is how the callback finds the attempt it belongs to, so two attempts must
    never be able to share one. Note the SDK generates `state` inside a local
    stack frame and validates it there too, so the row is a record of the attempt
    rather than the thing the SDK reads back — the resume itself happens in
    process (app/mcp/pending.py explains why).
    """

    __tablename__ = "mcp_oauth_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    server_id: Mapped[str] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True
    )
    server_url: Mapped[str] = mapped_column(Text, default="")
    # Encrypted JSON, never plaintext. See the class docstring.
    client_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    server: Mapped["McpServer"] = relationship(back_populates="oauth_sessions")


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
