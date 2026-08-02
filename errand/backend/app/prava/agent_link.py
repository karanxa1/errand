"""Agent linking — how this backend becomes an agent on a user's Prava wallet.

The wallet API will not talk to an anonymous caller. It talks to an AGENT: an
Ed25519 keypair that a human approved, once, inside their Prava wallet. That
approval is what makes a later `shop/checkout` a spend the user consented to
rather than one this server invented.

The handshake, mirroring `prava setup` (`src/commands/setup.ts`):

  1. Mint a keypair locally. The private key never leaves this machine.
  2. `POST /v1/agents/link/create` with the public key plus a signature over the
     lid-less canonical, which proves we hold the matching private key. The
     server issues an opaque `lid`.
  3. Show the human `https://pay.prava.space/link-agent?lid=<lid>` — they log
     into their wallet and approve. The link is good for 15 minutes.
  4. Poll `GET /v1/agents/link/status?lid=<lid>` until it reports `approved`,
     which carries the `agent_id` we sign as from then on.

Both endpoints are UNSIGNED at the header level (there is no agent id yet); the
proof is the `sig` field in the create body.

Note the host split: linking is on the API server (`api.prava.space`), while
shopping is on the wallet API (`pay-api.prava.space`). They are different
services and the CLI treats them as such.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Literal

import httpx

from app.prava.signing import KeyPair, generate_keypair, sign_create_params

DEFAULT_API_BASE = "https://api.prava.space"
DEFAULT_DASHBOARD_BASE = "https://pay.prava.space"

# The server's own TTL for a pending link, per `prava setup`.
LINK_TTL_S = 15 * 60
_POLL_INITIAL_INTERVAL_S = 3.0
_POLL_MAX_INTERVAL_S = 20.0

LinkStatus = Literal["pending", "approved", "denied", "expired"]


class AgentLinkError(RuntimeError):
    """A linking step failed, with a message safe to show an operator."""


@dataclass(frozen=True)
class PendingLink:
    """A link waiting on human approval. Persist this before showing the URL."""

    lid: str
    link_url: str
    keys: KeyPair
    created_at: float
    expires_at: str | None = None


@dataclass(frozen=True)
class LinkedAgent:
    """An approved agent identity — the credential pair the wallet client needs."""

    agent_id: str
    private_key: str
    public_key: str


async def create_link(
    *,
    name: str,
    platform: str = "custom",
    description: str = "",
    api_base: str = DEFAULT_API_BASE,
    dashboard_base: str = DEFAULT_DASHBOARD_BASE,
    keys: KeyPair | None = None,
) -> PendingLink:
    """Register a pending agent link and return the URL for the human to approve.

    `platform` follows Prava's agent-platform vocabulary; `custom` is the honest
    value for a first-party backend that is not one of the known agent hosts.
    """
    keys = keys or generate_keypair()
    iat = int(time.time())
    sig = sign_create_params(
        keys.private_key,
        public_key=keys.public_key,
        name=name,
        platform=platform,
        description=description,
        iat=iat,
    )
    body = {
        "public_key": keys.public_key,
        "name": name,
        "platform": platform,
        "description": description,
        "iat": iat,
        "sig": sig,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(
                f"{api_base.rstrip('/')}/v1/agents/link/create", json=body
            )
        except httpx.HTTPError as exc:
            raise AgentLinkError(
                f"Could not reach the Prava API to create a link: {exc}"
            ) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    lid = payload.get("lid") if isinstance(payload, dict) else None
    if response.status_code >= 400 or not lid:
        error = (payload or {}).get("error") or {}
        code = error.get("code")
        message = error.get("message")
        if code in ("LINK_EXPIRED", "LINK_FUTURE_IAT"):
            raise AgentLinkError(
                "Prava rejected the link timestamp — this machine's clock is out "
                "of sync. Fix the clock and retry."
            )
        raise AgentLinkError(
            f"Failed to create the agent link: {message or f'HTTP {response.status_code}'}"
        )

    return PendingLink(
        lid=lid,
        link_url=f"{dashboard_base.rstrip('/')}/link-agent?lid={lid}",
        keys=keys,
        created_at=time.time(),
        expires_at=payload.get("expires_at"),
    )


async def check_link(lid: str, *, api_base: str = DEFAULT_API_BASE) -> tuple[LinkStatus, str | None]:
    """One status read. Returns `(status, agent_id_or_None)`.

    A transport failure is reported as `pending` rather than raised: the caller
    is a poll loop bounded by the link TTL, and a blip should cost one tick, not
    the whole link.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(
                f"{api_base.rstrip('/')}/v1/agents/link/status", params={"lid": lid}
            )
        except httpx.HTTPError:
            return "pending", None
    try:
        payload = response.json()
    except ValueError:
        return "pending", None
    if not isinstance(payload, dict):
        return "pending", None
    status = payload.get("status")
    if status not in ("pending", "approved", "denied", "expired"):
        return "pending", None
    agent_id = payload.get("agent_id")
    return status, agent_id if isinstance(agent_id, str) else None


async def await_approval(
    pending: PendingLink,
    *,
    api_base: str = DEFAULT_API_BASE,
    timeout_s: float = LINK_TTL_S,
    on_tick: object = None,
) -> LinkedAgent:
    """Poll until the human approves, with the CLI's 1.5x backoff (3s → 20s cap).

    Raises AgentLinkError on denial, expiry, or the wall-clock running out.
    """
    deadline = time.monotonic() + timeout_s
    interval = _POLL_INITIAL_INTERVAL_S
    while time.monotonic() < deadline:
        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        if callable(on_tick):
            on_tick()
        status, agent_id = await check_link(pending.lid, api_base=api_base)
        if status == "approved" and agent_id:
            return LinkedAgent(
                agent_id=agent_id,
                private_key=pending.keys.private_key,
                public_key=pending.keys.public_key,
            )
        if status == "denied":
            raise AgentLinkError("The user denied this agent link.")
        if status == "expired":
            raise AgentLinkError("The agent link expired before it was approved.")
        interval = min(interval * 1.5, _POLL_MAX_INTERVAL_S)
    raise AgentLinkError("The agent link expired before it was approved.")


def env_block(agent: LinkedAgent) -> str:
    """Render the approved identity as the .env lines the backend reads.

    The private key is a bearer credential for spending the user's card. It
    belongs in the same gitignored `errand/.env` as every other secret here, and
    nowhere else.
    """
    return "\n".join(
        [
            "# Prava wallet agent (LIVE — this identity can spend a real card).",
            f"PRAVA_AGENT_ID={agent.agent_id}",
            f"PRAVA_AGENT_PRIVATE_KEY={agent.private_key}",
            "USE_PRAVA_SHOP=true",
        ]
    )


def as_json(agent: LinkedAgent) -> str:
    """The identity in the CLI's `~/.prava/agent.json` shape, for interop."""
    return json.dumps(
        {
            "agentId": agent.agent_id,
            "publicKey": agent.public_key,
            "privateKey": agent.private_key,
            "linked": True,
        },
        indent=2,
    )
