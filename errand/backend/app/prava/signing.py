"""Ed25519 agent identity — keygen, request signing, and the link canonicals.

Every byte here is dictated by the Prava CLI (`@prava-sdk/cli` v3.1.0), whose
signatures the wallet backend verifies. The reference implementations are
`src/crypto/keys.ts` and `src/crypto/link-sig.ts`; a divergence of one character
in a canonical string, or one byte in a key encoding, produces a signature the
server rejects with AUTH_INVALID_SIGNATURE and no further explanation.

The three contracts, restated so a future edit has something to check against:

  * Keys are Ed25519. The PRIVATE key travels as base64 of its DER PKCS8
    encoding; the PUBLIC key as base64 of its DER SPKI encoding. Not raw 32-byte
    seeds, not PEM.
  * A request signature signs the ASCII concatenation `timestamp + body`, where
    `timestamp` is Unix SECONDS rendered as a decimal string and `body` is the
    EXACT serialized request body — the same bytes that go on the wire. Signing a
    re-serialized copy is the classic way to break this: `json.dumps` with
    default separators emits `", "` where the CLI's `JSON.stringify` emits `","`,
    so the signed message and the sent body differ and the server rejects it. The
    caller therefore hands us the already-serialized string.
    Output is standard base64 (padded).
  * A link signature signs a canonical query-ish string of the link parameters,
    ordered `d,iat,lid,n,p,pk` (alphabetical by short key), each value
    percent-encoded the way JavaScript's `encodeURIComponent` does it. Output is
    base64url with padding stripped.

`encodeURIComponent` is the subtle one: Python's `urllib.parse.quote` escapes a
different set by default. The characters JS leaves unescaped are
`A-Z a-z 0-9 - _ . ! ~ * ' ( )`, so that exact safe-set is spelled out below.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# The unreserved set of JavaScript's encodeURIComponent. Anything outside it is
# percent-encoded. Python's quote() defaults to safe="/" and does NOT escape "/",
# so passing safe explicitly is required, not cosmetic.
_JS_URI_SAFE = "-_.!~*'()"


def js_encode_uri_component(value: str) -> str:
    """Percent-encode exactly the way JavaScript's encodeURIComponent does."""
    return quote(value, safe=_JS_URI_SAFE, encoding="utf-8")


@dataclass(frozen=True)
class KeyPair:
    """Base64 DER keys, in the shape the CLI stores and the server expects."""

    public_key: str  # base64(DER SPKI)
    private_key: str  # base64(DER PKCS8)


def generate_keypair() -> KeyPair:
    """Mint a fresh Ed25519 agent identity."""
    private = Ed25519PrivateKey.generate()
    return KeyPair(
        public_key=base64.b64encode(
            private.public_key().public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode(),
        private_key=base64.b64encode(
            private.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode(),
    )


def load_private_key(private_key_b64: str) -> Ed25519PrivateKey:
    """Parse a base64 DER PKCS8 Ed25519 private key.

    Raises ValueError with a message safe to log — never the key material.
    """
    try:
        der = base64.b64decode(private_key_b64, validate=True)
    except Exception as exc:  # noqa: BLE001 — base64 raises several types
        raise ValueError("Prava agent private key is not valid base64.") from exc
    try:
        key = serialization.load_der_private_key(der, password=None)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            "Prava agent private key is not a DER PKCS8 key (expected base64 of "
            "the PKCS8 DER encoding, as written by `prava setup`)."
        ) from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(
            f"Prava agent private key must be Ed25519, got {type(key).__name__}."
        )
    return key


def public_key_b64(private_key_b64: str) -> str:
    """Derive the base64 DER SPKI public key from a stored private key."""
    return base64.b64encode(
        load_private_key(private_key_b64)
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).decode()


def sign_request(private_key_b64: str, timestamp: str, body: str) -> str:
    """Sign `timestamp + body` for the X-Signature header (standard base64).

    `body` MUST be the exact string that will be sent as the request body — see
    the module docstring on why re-serializing breaks this.
    """
    signature = load_private_key(private_key_b64).sign((timestamp + body).encode())
    return base64.b64encode(signature).decode()


def verify_request(
    public_key_b64_value: str, timestamp: str, body: str, signature_b64: str
) -> bool:
    """Verify a request signature. Used by the tests, and by nothing else."""
    try:
        der = base64.b64decode(public_key_b64_value, validate=True)
        key = serialization.load_der_public_key(der)
        if not isinstance(key, Ed25519PublicKey):
            return False
        key.verify(base64.b64decode(signature_b64), (timestamp + body).encode())
        return True
    except Exception:  # noqa: BLE001 — any failure is a failed verification
        return False


def _b64url_unpadded(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def canonical_create_message(
    *, public_key: str, name: str, platform: str, description: str, iat: int
) -> str:
    """Canonical string for `POST /v1/agents/link/create`.

    Lid-less: the backend issues the link id, so the CLI cannot include it. Field
    order is fixed (d, iat, n, p, pk) and must match the backend verifier.
    """
    enc = js_encode_uri_component
    return (
        f"d={enc(description)}"
        f"&iat={iat}"
        f"&n={enc(name)}"
        f"&p={enc(platform)}"
        f"&pk={enc(public_key)}"
    )


def canonical_link_message(
    *, lid: str, public_key: str, name: str, platform: str, description: str, iat: int
) -> str:
    """Canonical string for a link URL, which carries the server-issued lid."""
    enc = js_encode_uri_component
    return (
        f"d={enc(description)}"
        f"&iat={iat}"
        f"&lid={enc(lid)}"
        f"&n={enc(name)}"
        f"&p={enc(platform)}"
        f"&pk={enc(public_key)}"
    )


def sign_create_params(
    private_key_b64: str,
    *,
    public_key: str,
    name: str,
    platform: str,
    description: str,
    iat: int,
) -> str:
    """Sign the link-create canonical. base64url, padding stripped."""
    message = canonical_create_message(
        public_key=public_key,
        name=name,
        platform=platform,
        description=description,
        iat=iat,
    ).encode()
    return _b64url_unpadded(load_private_key(private_key_b64).sign(message))


def sign_link_params(
    private_key_b64: str,
    *,
    lid: str,
    public_key: str,
    name: str,
    platform: str,
    description: str,
    iat: int,
) -> str:
    """Sign the lid-bearing link canonical. base64url, padding stripped."""
    message = canonical_link_message(
        lid=lid,
        public_key=public_key,
        name=name,
        platform=platform,
        description=description,
        iat=iat,
    ).encode()
    return _b64url_unpadded(load_private_key(private_key_b64).sign(message))
