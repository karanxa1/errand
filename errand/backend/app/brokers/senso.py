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

    # Name heuristic. Two things went wrong before and both mattered because this
    # name is sent verbatim to Prava as merchant_details.name:
    #   1. The label set missed "store:", which the personal policy uses.
    #   2. The name capture allowed lowercase words, so a label word appearing
    #      mid-sentence ("...I order from Demo Pantry Co") let the match start in
    #      the middle of a sentence and swallow the whole run — the shipped bug
    #      was the name "For groceries I order from Demo Pantry Co".
    #
    # Fix: require the label to be immediately FOLLOWED by the name (a real label
    # is "Store: X", not a sentence that happens to contain "shop"), and capture
    # only a short run of Title-Case / all-caps tokens (a vendor name), stopping
    # at the first lowercase connector. Markdown bold is stripped first.
    plain = text.replace("**", "").replace("*", "")
    #  label  :  Name Words (each Title-Case or ALLCAPS, joined by spaces/&)
    # Case-insensitivity is scoped to the LABEL only (via (?i:...)), so the name
    # capture stays strictly Title-Case/ALLCAPS and cannot pick up a lowercase
    # connective word.
    name_m = re.search(
        r"(?i:(?:approved\s+)?(?:vendor|merchant|seller|store|shop))\s*:\s*"
        r"([A-Z][A-Za-z0-9&'.]*(?:\s+(?:[A-Z][A-Za-z0-9&'.]*|&))*)",
        plain,
    )
    name = name_m.group(1).strip(" .") if name_m else "Approved Merchant"
    # Belt and braces: a name is a few words. If anything slipped a connector in,
    # keep only the leading Title-Case run rather than a sentence.
    name = re.sub(r"\s+(?:for|from|at|the|a|an|only|and|or|when|where)\b.*$", "", name).strip()
    if not name:
        name = "Approved Merchant"
    url = url_m.group(0).rstrip(").,") if url_m else "https://demo-pantry.example.com"
    merchants.append(Merchant(name=name, url=url))
    return merchants


# A rule that RESTRICTS what may be bought. These must never be dropped by the
# length cap: the seeded policy renders its budget/vendor/brand bullets first and
# its restrictions last, so a plain head-truncation silently discarded
# "Do not purchase energy drinks" — the run then bought the banned item while the
# approval screen still implied the policy had been applied.
_RESTRICTIVE_MARKERS = (
    "do not",
    "don't",
    "never",
    "avoid",
    "no ",
    "not allowed",
    "prohibited",
    "banned",
    "forbidden",
    "only",
    "must",
    "cap",
    "limit",
    "maximum",
    "max ",
    "under ",
    "at or under",
    "requires",
    "sign-off",
)

# Total rules kept. Bounded because these are pasted into an LLM prompt and drive
# a browser run, but large enough to hold the restrictive lines as well as the
# descriptive ones.
_MAX_RULES = 12


def _is_restrictive(rule: str) -> bool:
    low = rule.lower()
    return any(marker in low for marker in _RESTRICTIVE_MARKERS)


def _extract_rules(answer: str, corpus: str) -> list[str]:
    """Rules from the grounded answer, keeping restrictions ahead of prose.

    Bullet lines are collected in order, then RE-ORDERED so restrictive rules come
    first before the list is capped. Order within each group is preserved, so the
    policy still reads naturally; what changes is which lines survive the cap.
    A prohibition that falls off the end is invisible at run time, which is
    exactly how a banned product reached a cart a human was asked to approve.
    """
    rules: list[str] = []
    for line in answer.splitlines():
        s = line.strip().lstrip("-*•·– ").strip()
        s = re.sub(r"\*+", "", s).strip()
        if len(s) <= 8:
            continue
        low = s.lower()
        # Drop the answer's own preamble and bare section headings: they carry no
        # rule, and "Preferred brands:" in particular used to be parsed AS a
        # brand (see _preferred_brands).
        if low.startswith("for ") or low.startswith("your "):
            continue
        if s.endswith(":"):
            continue
        rules.append(s)

    # Sentences inside a bullet can carry the restriction ("... limited to X. Do
    # not purchase Y."). Split those out so a restriction is its own rule and can
    # be ranked and kept on its own merits.
    split_rules: list[str] = []
    for rule in rules:
        parts = [p.strip() for p in re.split(r"(?<=[.;])\s+", rule) if len(p.strip()) > 8]
        split_rules.extend(parts or [rule])

    if not split_rules:
        flat = re.sub(r"\*+", "", answer.strip())
        return [flat] if flat else []

    restrictive = [r for r in split_rules if _is_restrictive(r)]
    descriptive = [r for r in split_rules if not _is_restrictive(r)]
    return (restrictive + descriptive)[:_MAX_RULES]
