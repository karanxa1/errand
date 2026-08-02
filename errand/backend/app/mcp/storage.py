"""DB-backed TokenStorage, so OAuth consent survives a restart.

`TokenStorage` is the MCP SDK's persistence seam — a Protocol with four async
methods, no base class to inherit (mcp.client.auth.oauth2.TokenStorage). The SDK
docs show an `InMemoryTokenStorage`; using that here would mean every deploy
silently re-runs the whole consent flow, asking the user to reauthorize a server
they already authorized and leaving a fresh dynamic client registration behind on
the remote server each time. So it is backed by `mcp_oauth_sessions`.

Both values are ENCRYPTED before they are written (app/mcp/crypto.py). The token
set normally contains a refresh token — durable access to a third-party account,
not a value that expires in an hour — and the client record can contain an issued
`client_secret`.

Each method opens its OWN short-lived session. It has to: these are called from
inside a live OAuth flow, which is running under a connect() that may itself be
inside an SSE stream whose request-scoped session was closed the moment the
response body started streaming. Reusing that session would be a use-after-close.
This is the same reason app/routers/chat.py opens SessionLocal() for its approval
polling, and the pattern is deliberately identical.
"""

from __future__ import annotations

import logging

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import select

from app.db import SessionLocal
from app.mcp.crypto import decrypt_json, encrypt_json
from app.models import McpOAuthSession

logger = logging.getLogger("errand.mcp.storage")


class DbTokenStorage:
    """`TokenStorage` for one MCP server, persisted and encrypted.

    Deliberately structurally typed rather than declared as implementing the
    Protocol: the SDK checks shape, and inheriting would couple us to a private
    import path.
    """

    def __init__(self, server_id: str, server_url: str) -> None:
        self._server_id = server_id
        self._server_url = server_url

    async def _row(self, session) -> McpOAuthSession | None:
        return (
            await session.scalars(
                select(McpOAuthSession)
                .where(McpOAuthSession.server_id == self._server_id)
                .order_by(McpOAuthSession.updated_at.desc())
            )
        ).first()

    async def _upsert(self, **values) -> None:
        async with SessionLocal() as session:
            row = await self._row(session)
            if row is None:
                row = McpOAuthSession(
                    server_id=self._server_id, server_url=self._server_url
                )
                session.add(row)
            for key, value in values.items():
                setattr(row, key, value)
            await session.commit()

    # ── the four TokenStorage methods ───────────────────────────────────────────

    async def get_tokens(self) -> OAuthToken | None:
        async with SessionLocal() as session:
            row = await self._row(session)
            payload = decrypt_json(row.tokens) if row is not None else None
        if not payload:
            return None
        try:
            return OAuthToken.model_validate(payload)
        except Exception:  # noqa: BLE001 — a stored shape the SDK no longer accepts
            logger.warning(
                "Stored OAuth tokens for server %s no longer validate; treating "
                "them as absent so the server can be re-authorized.",
                self._server_id,
            )
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        await self._upsert(tokens=encrypt_json(tokens.model_dump(mode="json")))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        async with SessionLocal() as session:
            row = await self._row(session)
            payload = decrypt_json(row.client_info) if row is not None else None
        if not payload:
            return None
        try:
            return OAuthClientInformationFull.model_validate(payload)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Stored OAuth client registration for server %s no longer "
                "validates; a fresh registration will be performed.",
                self._server_id,
            )
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await self._upsert(
            client_info=encrypt_json(client_info.model_dump(mode="json"))
        )


async def has_tokens(server_id: str) -> bool:
    """Whether this server already holds a usable token set.

    Read by the connect path to decide whether to attach the OAuth provider at
    all, and by the API to report `authorizing` vs `connected` without a network
    call. A token that fails to decrypt reads as absent, which is the safe answer:
    the user is asked to authorize again rather than being told they are connected
    when the credential is unreadable.
    """
    async with SessionLocal() as session:
        row = (
            await session.scalars(
                select(McpOAuthSession)
                .where(McpOAuthSession.server_id == server_id)
                .order_by(McpOAuthSession.updated_at.desc())
            )
        ).first()
        if row is None:
            return False
        return bool(decrypt_json(row.tokens))


async def clear_tokens(server_id: str) -> None:
    """Forget this server's credentials, leaving the server row in place.

    Backs the UI's "Disconnect": the user keeps the server they configured and
    drops only the authorization. The dynamic client registration goes too — it
    was minted for a consent that no longer exists, and keeping it would let a
    later connect reuse a registration the user believes they revoked.
    """
    async with SessionLocal() as session:
        rows = list(
            await session.scalars(
                select(McpOAuthSession).where(McpOAuthSession.server_id == server_id)
            )
        )
        for row in rows:
            await session.delete(row)
        await session.commit()


async def record_state(server_id: str, server_url: str, state: str | None) -> None:
    """Note the OAuth state for the current attempt, for auditability.

    The SDK validates `state` inside its own stack frame (see app/mcp/pending.py),
    so nothing reads this back to make a security decision — it is a record of
    which attempt a session row belongs to, not the check itself. Written on a
    best-effort basis: the column is unique, and a collision means a concurrent
    attempt already claimed it, which must not fail the flow.
    """
    try:
        async with SessionLocal() as session:
            row = (
                await session.scalars(
                    select(McpOAuthSession)
                    .where(McpOAuthSession.server_id == server_id)
                    .order_by(McpOAuthSession.updated_at.desc())
                )
            ).first()
            if row is None:
                row = McpOAuthSession(server_id=server_id, server_url=server_url)
                session.add(row)
            row.state = state
            await session.commit()
    except Exception:  # noqa: BLE001 — bookkeeping must never break the flow
        logger.debug("Could not record OAuth state for server %s", server_id)
