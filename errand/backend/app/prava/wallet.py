"""Signed HTTP client for the Prava wallet API (`pay-api.prava.space`).

Every wallet call is authenticated by an Ed25519 signature over
`timestamp + body`, so the body has to be serialized EXACTLY ONCE and both
signed and sent as those same bytes. That is the single most breakable thing in
this file, and the reason `post()` takes a dict and does the serialization
itself rather than accepting `json=` like the rest of the codebase's httpx
calls: an httpx `json=` kwarg re-serializes with `", "` separators, which would
not match what we signed, and the server would answer AUTH_INVALID_SIGNATURE
with nothing to say why.

Responses are a uniform envelope — `{success, data}` on the way up,
`{success: false, error: {code, message}}` (or a bare `{error: …}` from the
auth layer) on the way down, plus a `replayed` flag on an idempotent repeat of a
terminal checkout. `post()` normalizes all of that into either the `data`
payload or a raised `WalletError`.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.prava.signing import sign_request

logger = logging.getLogger(__name__)

DEFAULT_WALLET_API_BASE = "https://pay-api.prava.space"

# The wallet's own client budget. `quote` and `checkout` drive a real browser on
# the merchant and routinely take 20-40s, so they get the longer budget the CLI
# uses (45s, matching the server-side budget); everything else uses 30s.
DEFAULT_TIMEOUT_S = 30.0
BROWSER_TIMEOUT_S = 45.0


class WalletError(RuntimeError):
    """A wallet API call that did not succeed, with the server's safe message.

    `code` is the wallet's machine-readable error code when it sent one
    (AUTH_INVALID_SIGNATURE, SHOP_SESSION_EXPIRED, SHOP_ADDRESS_REQUIRED,
    SHOP_CHECKOUT_IN_PROGRESS, …). `replayed` marks the idempotent repeat of a
    terminal checkout — the caller must NOT treat that as a fresh charge.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        replayed: bool = False,
    ) -> None:
        self.code = code
        self.status = status
        self.replayed = replayed
        super().__init__(message)


def _extract_error(payload: Any) -> tuple[str | None, str | None]:
    """Pull (code, message) out of the several error shapes the wallet emits."""
    if not isinstance(payload, dict):
        return None, None
    err = payload.get("error")
    if err is None:
        return None, None
    if isinstance(err, str):
        return None, err
    if isinstance(err, dict):
        code = err.get("code")
        message = err.get("message")
        return (
            code if isinstance(code, str) else None,
            message if isinstance(message, str) else None,
        )
    return None, None


class WalletClient:
    """Agent-signed client for `/v1/wallet/*`.

    Construct one per run: it holds no per-request state, but the identity it
    signs with is the operator's linked agent and should not be shared across
    tenants.
    """

    def __init__(
        self,
        agent_id: str,
        private_key: str,
        *,
        base_url: str = DEFAULT_WALLET_API_BASE,
        skill_name: str = "prava-shopping",
    ) -> None:
        if not agent_id:
            raise ValueError("Prava wallet client needs a linked agent id.")
        if not private_key:
            raise ValueError("Prava wallet client needs the agent private key.")
        self._agent_id = agent_id
        self._private_key = private_key
        self._base = base_url.rstrip("/")
        self._skill_name = skill_name

    def _headers(self, body: str) -> dict[str, str]:
        # Unix SECONDS, as a decimal string — the server rejects a millisecond
        # timestamp as expired, since it reads it as a date ~55,000 years hence.
        timestamp = str(int(time.time()))
        return {
            "Content-Type": "application/json",
            "X-Skill-Name": self._skill_name,
            "X-Agent-Id": self._agent_id,
            "X-Timestamp": timestamp,
            "X-Signature": sign_request(self._private_key, timestamp, body),
        }

    async def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """POST a signed request and return the envelope's `data`.

        Raises WalletError on any non-success outcome. A 2xx whose envelope says
        `success: false` is a failure too — the wallet reports declines that way.
        """
        # Serialize ONCE. These bytes are both what we sign and what we send.
        payload = json.dumps(body or {}, separators=(",", ":"), ensure_ascii=False)
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            try:
                response = await client.post(
                    f"{self._base}{path}",
                    headers=self._headers(payload),
                    content=payload.encode("utf-8"),
                )
            except httpx.TimeoutException as exc:
                raise WalletError(
                    "The Prava wallet did not respond in time — the merchant "
                    "checkout can be slow.",
                    code="WALLET_TIMEOUT",
                ) from exc
            except httpx.HTTPError as exc:
                raise WalletError(
                    f"Could not reach the Prava wallet: {exc}", code="WALLET_UNREACHABLE"
                ) from exc

        try:
            envelope = response.json()
        except ValueError:
            envelope = {}

        replayed = bool(isinstance(envelope, dict) and envelope.get("replayed"))
        code, message = _extract_error(envelope)
        succeeded = (
            response.status_code < 400
            and isinstance(envelope, dict)
            and envelope.get("success") is not False
            and code is None
        )
        if not succeeded:
            raise WalletError(
                message or f"Prava wallet returned HTTP {response.status_code}.",
                code=code,
                status=response.status_code,
                replayed=replayed,
            )

        data = envelope.get("data") if isinstance(envelope, dict) else None
        # A few wallet endpoints (addresses/list) answer flat rather than wrapped.
        return data if isinstance(data, dict) else (envelope if isinstance(envelope, dict) else {})
