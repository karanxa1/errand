#!/usr/bin/env python
"""Link this backend to a Prava wallet as an agent — the production shop path.

    uv run python scripts/prava_link.py --name "Errand"

Mints an Ed25519 identity, registers a pending link, prints the URL for the
human to approve in their Prava wallet, and waits. On approval it prints the two
env lines to paste into `errand/.env`.

READ THIS BEFORE RUNNING IT. The wallet API has no sandbox: an approved agent
shops REAL merchants and charges a REAL card. That is the whole point of the
production path, and it is why nothing here is wired on by default. For sandbox
work you do not need this script at all — the sandbox demo runs the storefront
shopper with sk_test_ card sessions, and `scripts/verify_prava_sandbox.py` is
what exercises that.

The private key it prints is a bearer credential for spending that card. It goes
in the gitignored errand/.env and nowhere else — not a commit, not a log, not a
chat.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.prava.agent_link import (  # noqa: E402
    AgentLinkError,
    await_approval,
    create_link,
    env_block,
)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Link this backend to a Prava wallet.")
    parser.add_argument("--name", default="Errand", help="Agent name shown to the user.")
    parser.add_argument(
        "--platform",
        default="custom",
        help="Prava agent platform vocabulary; 'custom' for a first-party backend.",
    )
    parser.add_argument(
        "--description",
        default="Errand — policy-grounded procurement with human approval",
        help="Shown to the user on the approval screen.",
    )
    args = parser.parse_args()

    if settings.prava_agent_id:
        print(f"Already linked as agent {settings.prava_agent_id}.")
        print("Remove PRAVA_AGENT_ID / PRAVA_AGENT_PRIVATE_KEY from .env to re-link.")
        return 0

    try:
        pending = await create_link(
            name=args.name,
            platform=args.platform,
            description=args.description,
            api_base=settings.prava_link_api_base,
            dashboard_base=settings.prava_dashboard_base,
        )
    except AgentLinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print()
    print("Open this URL and approve the agent in your Prava wallet:")
    print()
    print(f"    {pending.link_url}")
    print()
    print("The link expires in 15 minutes. Waiting for approval", end="", flush=True)

    try:
        agent = await await_approval(
            pending,
            api_base=settings.prava_link_api_base,
            on_tick=lambda: print(".", end="", flush=True),
        )
    except AgentLinkError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    print(f"\n\nLinked. Agent id: {agent.agent_id}")
    print("\nAdd these to errand/.env (gitignored — the key can spend a real card):\n")
    print(env_block(agent))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
