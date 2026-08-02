#!/usr/bin/env python
"""Enumerate the merchants Prava's catalog can actually reach.

    cd backend && uv run python -m scripts.prava_merchants
    cd backend && uv run python -m scripts.prava_merchants --queries "coffee,socks,dog food"

There is no "list merchants" endpoint — Prava's catalog is a UCP index, queried
by product, not browsed by seller. So the only honest way to answer "which
merchants can we buy from" is to search a spread of categories and collect the
distinct domains that come back. That is what this does, and it is a SAMPLE, not
a registry: a merchant that stocks nothing matching these probes will not appear
even though it is perfectly buyable.

REQUIRES A LINKED AGENT. `/v1/wallet/shop/search` is agent-signed against
pay-api.prava.space, and that host is production-only — an unauthenticated probe
answers 401 AUTH_INVALID_SIGNATURE. Run scripts/prava_link.py first. Searching
costs nothing and buys nothing: `search` and `product` are reads. Only `quote`
and `checkout` touch a merchant's browser or a card.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.brokers.prava_shop import MAX_PRODUCTS_INSPECTED  # noqa: E402
from app.config import settings  # noqa: E402
from app.prava.wallet import WalletClient, WalletError  # noqa: E402

# A spread wide enough that the sample says something about coverage rather than
# about one aisle. Deliberately ordinary consumer categories — the things an
# errand actually asks for.
DEFAULT_QUERIES = [
    "coffee beans",
    "office paper",
    "dog food",
    "protein bars",
    "sparkling water",
    "phone charger",
    "t shirt",
    "notebook",
    "hand soap",
    "batteries",
    "tea",
    "socks",
]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--queries",
        default="",
        help="Comma-separated probes to use instead of the built-in spread.",
    )
    parser.add_argument("--limit", type=int, default=MAX_PRODUCTS_INSPECTED)
    parser.add_argument("--ships-to", default=settings.prava_ships_to)
    args = parser.parse_args()

    if not (settings.prava_agent_id and settings.prava_agent_private_key):
        print(
            "No linked agent. The catalog is agent-signed, so this cannot run "
            "unauthenticated.\nRun: uv run python scripts/prava_link.py --name \"Errand\"",
            file=sys.stderr,
        )
        return 2

    queries = [q.strip() for q in args.queries.split(",") if q.strip()] or DEFAULT_QUERIES
    client = WalletClient(
        settings.prava_agent_id,
        settings.prava_agent_private_key,
        base_url=settings.prava_wallet_api_base,
    )

    # domain -> (hits, one example title)
    merchants: dict[str, tuple[int, str]] = {}
    failures: list[str] = []

    for query in queries:
        try:
            data = await client.post(
                "/v1/wallet/shop/search",
                {"query": query, "limit": args.limit, "shipsTo": args.ships_to},
            )
        except WalletError as exc:
            failures.append(f"{query}: {exc}")
            print(f"  {query:<18} — failed: {exc}")
            continue

        results = [r for r in (data.get("results") or []) if isinstance(r, dict)]
        found: set[str] = set()
        for result in results:
            domain = str(result.get("merchant") or "").lower().strip()
            if not domain:
                continue
            found.add(domain)
            hits, example = merchants.get(domain, (0, ""))
            merchants[domain] = (hits + 1, example or str(result.get("title") or ""))
        print(f"  {query:<18} — {len(results)} results across {len(found)} merchant(s)")

    print(f"\n{len(merchants)} distinct merchant(s) reachable from {len(queries)} probes:\n")
    for domain, (hits, example) in sorted(merchants.items(), key=lambda kv: -kv[1][0]):
        print(f"  {domain:<38} {hits:>3} hit(s)   e.g. {example[:52]}")

    if failures:
        print(f"\n{len(failures)} probe(s) failed:")
        for failure in failures:
            print(f"  {failure}")

    print(
        "\nThis is a SAMPLE, not a registry — Prava indexes by product, so a "
        "merchant stocking nothing matching these probes is absent here and "
        "still buyable. Widen it with --queries."
    )
    return 0 if merchants else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
