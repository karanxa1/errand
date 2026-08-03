"""Encryption at rest for MCP credentials.

Three of the values this feature stores are live credentials to a third party:

  * the static header secrets for an `auth_mode='headers'` server (a bearer
    token, an API key),
  * the OAuth token set — which usually includes a REFRESH token, i.e. durable
    access to someone's account, not a value that expires in an hour,
  * the dynamically-registered client record, which can carry a `client_secret`.

None of those belong in the database as readable JSON. They are encrypted here
with Fernet (AES-128-CBC + HMAC-SHA256, authenticated, from `cryptography`,
already a dependency of this backend for the Prava Ed25519 signing path).

KEY MANAGEMENT, and the deliberate choice made here
---------------------------------------------------
The key comes from MCP_ENCRYPTION_KEY when set. When it is NOT set we derive one
from JWT_SECRET via HKDF, under a distinct `info` label so the derived key is
cryptographically unrelated to the signing secret it came from.

That derivation is a convenience, not a shrug: this deployment already refuses to
start unless JWT_SECRET is a long, non-default random string (see
Settings.jwt_secret_problem), so the derived key inherits real entropy and the
feature works on a fresh deploy without a second secret to provision. The
trade-off is honest and worth stating: ROTATING JWT_SECRET WOULD ORPHAN EVERY
STORED CREDENTIAL. They are recoverable — the user re-enters a header secret or
re-authorizes an OAuth server — but they do not survive the rotation. Set
MCP_ENCRYPTION_KEY explicitly to decouple the two lifecycles, which is the right
move for any deployment that rotates its JWT secret on a schedule.

A value that cannot be decrypted is treated as absent rather than fatal: an
orphaned credential must degrade to "this server needs authorizing again", never
to a 500 that takes the whole tool list down with it.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger("errand.mcp.crypto")

# Distinguishes this key's derivation from any other use of the same input
# secret. Changing this string invalidates every previously stored credential.
_HKDF_INFO = b"errand-mcp-credential-encryption-v1"


class CredentialCryptoError(RuntimeError):
    """No usable encryption key is configured."""


def _fernet() -> Fernet:
    """The Fernet instance for this process.

    Not cached: `settings` is a module-level singleton and the tests rebind its
    fields to exercise both key paths. Constructing a Fernet is a couple of
    cheap key-schedule setups, and every call site here is already doing database
    or network work, so this is not on any hot path.
    """
    configured = (settings.mcp_encryption_key or "").strip()
    if configured:
        # A urlsafe-base64 32-byte key, i.e. exactly what Fernet.generate_key()
        # emits. Anything else is a misconfiguration worth naming precisely,
        # because the failure otherwise surfaces as an opaque binascii error.
        try:
            key = configured.encode("ascii")
            if len(base64.urlsafe_b64decode(key)) != 32:
                raise ValueError("wrong length")
            return Fernet(key)
        except Exception as exc:  # noqa: BLE001
            raise CredentialCryptoError(
                "MCP_ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
                "python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            ) from exc

    secret = (settings.jwt_secret or "").strip()
    if not secret:
        raise CredentialCryptoError(
            "Cannot encrypt MCP credentials: set MCP_ENCRYPTION_KEY, or a "
            "JWT_SECRET to derive it from."
        )
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_json(value: Any) -> str:
    """Serialize `value` and return it encrypted, as a str for a Text column."""
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def decrypt_json(blob: str | None) -> Any | None:
    """Decrypt a value written by `encrypt_json`, or None if it is unreadable.

    Unreadable means exactly one of two things in practice: the row predates a
    key change (see the module docstring on rotation), or it was corrupted. Both
    are recoverable by re-authorizing, and neither should be able to fail a
    request — so this logs and returns None rather than raising. The caller then
    treats the server as unauthenticated, which is the correct, safe reading.
    """
    if not blob:
        return None
    try:
        return json.loads(_fernet().decrypt(blob.encode("ascii")).decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, CredentialCryptoError):
        logger.warning(
            "Stored MCP credential could not be decrypted (key changed, or the "
            "value is corrupt). Treating it as absent; the server will need "
            "authorizing again."
        )
        return None


def encryption_available() -> bool:
    """Whether credentials can be stored at all.

    Called before accepting a secret, so a deployment with no usable key is told
    that up front instead of writing something it can never read back.
    """
    try:
        _fernet()
        return True
    except CredentialCryptoError:
        return False
