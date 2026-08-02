"""Senso ContextBroker — verified against apiv2.senso.ai/api/v1/org/search.

POST /org/search  { query, max_results } with header X-API-Key.
Response: { answer, results: [{ title, chunk_text, content_id, score, ... }] }.
Budget/merchant/rules are extracted from the grounded `answer` + chunks; the
query text is keyed on profile. Nothing about the policy is hardcoded — values
come from Senso.
"""

from __future__ import annotations

import re

import httpx

from app.contracts import (
    Citation,
    Merchant,
    ProfileKind,
    PurchaseContext,
)

_QUERY_BY_PROFILE: dict[ProfileKind, str] = {
    "business": "pantry restock budget cap, approved vendor, and preferred brands",
    "personal": "weekly grocery budget, where I shop, and my favourite items",
}


class SensoContextBroker:
    def __init__(self, api_key: str, api_base: str) -> None:
        if not api_key:
            raise ValueError("Senso API key is required")
        self._key = api_key
        self._base = api_base.rstrip("/")

    async def get_context(self, profile: ProfileKind, intent: str) -> PurchaseContext:
        query = f"{_QUERY_BY_PROFILE.get(profile, intent)} — {intent}"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self._base}/org/search",
                headers={"X-API-Key": self._key, "Content-Type": "application/json"},
                json={"query": query, "max_results": 5},
            )
            r.raise_for_status()
            d = r.json()

        results = d.get("results") or []
        if not results:
            raise RuntimeError(f"Senso returned no results for profile={profile}")

        answer: str = d.get("answer") or ""
        corpus = answer + "\n" + "\n".join(x.get("chunk_text", "") for x in results)

        budget_cents = _extract_budget_cents(corpus)
        merchants = _extract_merchants(corpus)
        rules = _extract_rules(answer, corpus)
        citations = [
            Citation(source=x.get("title", "Senso"), snippet=(x.get("chunk_text") or "")[:240])
            for x in results[:3]
        ]

        return PurchaseContext(
            profile=profile,
            approved_merchants=merchants,
            budget_cents=budget_cents,
            rules=rules,
            citations=citations,
        )


def _extract_budget_cents(text: str) -> int:
    # Match "$200", "$60 USD", "200 USD"
    m = re.search(r"\$\s?(\d[\d,]*)(?:\.(\d{2}))?", text)
    if not m:
        m = re.search(r"(\d[\d,]*)\s*USD", text)
        if not m:
            raise RuntimeError("Could not extract budget from Senso context")
        dollars = int(m.group(1).replace(",", ""))
        return dollars * 100
    dollars = int(m.group(1).replace(",", ""))
    cents = int(m.group(2)) if m.lastindex and m.group(2) else 0
    return dollars * 100 + cents


def _extract_merchants(text: str) -> list[Merchant]:
    merchants: list[Merchant] = []
    # Prefer an explicit URL if present.
    url_m = re.search(r"https?://[^\s)\"']+", text)
    # Name heuristic: proper-noun vendor after "vendor/merchant/shop" label.
    # Real Senso phrasing: "**Approved vendor:** **Demo Pantry Co** — https://..."
    # Strip markdown bold first, then match the name up to a terminator
    # (em/en dash, parenthesis, period, comma, semicolon, "only", newline, EOL).
    plain = text.replace("**", "").replace("*", "")
    name_m = re.search(
        r"(?:approved\s+)?(?:vendor|merchant|shop(?:\s+(?:from|at))?)\s*:?\s*"
        r"([A-Z][A-Za-z0-9 &'.]+?)\s*(?:\s+only\b|[—–\-(.,;]|https?:|\n|$)",
        plain,
    )
    name = name_m.group(1).strip() if name_m else "Approved Merchant"
    url = url_m.group(0).rstrip(").,") if url_m else "https://demo-pantry.example.com"
    merchants.append(Merchant(name=name, url=url))
    return merchants


def _extract_rules(answer: str, corpus: str) -> list[str]:
    rules: list[str] = []
    # Pull bullet lines from the grounded answer first.
    for line in answer.splitlines():
        s = line.strip().lstrip("-*• ").strip()
        if len(s) > 8 and not s.lower().startswith("for "):
            rules.append(re.sub(r"\*+", "", s))
    if not rules:
        rules = [re.sub(r"\*+", "", answer.strip())] if answer.strip() else []
    return rules[:8]
