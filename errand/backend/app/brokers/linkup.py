"""Linkup web-search broker — verified against api.linkup.so/v1/search.

POST /v1/search  { q, depth, outputType:"sourcedAnswer" } with Bearer auth.
Response: { answer, sources: [{ name, url, snippet, favicon }, ...] }.

Kept dependency-free (httpx only, already installed) so no extra SDK is needed;
the wire shape below is the exact `sourcedAnswer` output type Linkup returns.
The `web_search` voice tool consumes `search()` directly.
"""

from __future__ import annotations

import httpx


class LinkupSearchBroker:
    def __init__(self, api_key: str, api_base: str = "https://api.linkup.so/v1") -> None:
        if not api_key:
            raise ValueError("Linkup API key is required")
        self._key = api_key
        self._base = api_base.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    async def search(self, query: str, depth: str = "standard") -> dict:
        """Return a grounded answer plus its sources.

        Shape: { "answer": str, "sources": [{ "name", "url", "snippet" }, ...] }.
        `depth` is "standard" (fast) or "deep" (multi-iteration).
        """
        if depth not in ("standard", "deep"):
            depth = "standard"
        body = {"q": query, "depth": depth, "outputType": "sourcedAnswer"}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self._base}/search", headers=self._headers(), json=body
            )
            r.raise_for_status()
            d = r.json()

        answer: str = d.get("answer") or ""
        sources: list[dict] = []
        for s in d.get("sources") or []:
            sources.append(
                {
                    "name": s.get("name") or s.get("title") or "",
                    "url": s.get("url") or "",
                    "snippet": s.get("snippet") or s.get("content") or "",
                }
            )
        return {"answer": answer, "sources": sources}
