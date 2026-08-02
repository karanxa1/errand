"""Verify the Linkup web-search broker against the LIVE api.linkup.so API.

Run: cd backend && uv run python -m scripts.verify_linkup
Proves: a real grounded answer + non-empty sources come back. Exits non-zero on
failure. Mirrors scripts/verify_live.py.
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.brokers.linkup import LinkupSearchBroker


async def main() -> None:
    print("=== Linkup (live) ===")
    linkup = LinkupSearchBroker(settings.linkup_api_key, settings.linkup_api_base)
    query = "best rated standing desk under $400 2026"
    result = await linkup.search(query)
    answer = result["answer"]
    sources = result["sources"]

    print(f"  query: {query}")
    print(f"  answer: {answer[:300]}{'...' if len(answer) > 300 else ''}")
    print(f"  sources: {len(sources)}")
    for s in sources[:3]:
        print(f"    - {s['name']}  {s['url']}")

    assert answer.strip(), "expected a non-empty grounded answer"
    assert sources, "expected non-empty sources"

    print("\n✅ Live verification passed: Linkup web search works.")


if __name__ == "__main__":
    asyncio.run(main())
