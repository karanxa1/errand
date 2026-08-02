"""Policy semantics for cart selection — brand preferences and prohibitions.

Extracted from the browser shopper so BOTH shoppers apply the identical reading
of a policy: the Playwright one that walks a storefront's DOM, and the Prava
wallet one that shops the UCP catalog. Two copies of these regexes would be two
policies, and the drift would show up as a banned item quietly landing in a cart
on one path but not the other.

Every rule here was wrong in a shipped build at least once; the comments record
which real phrasing broke it, so re-simplifying one of them fails a test rather
than a live errand. See tests/test_spend_rules.py and tests/test_senso_rules.py.
"""

from __future__ import annotations

import re


# Words that are never a brand. "Preferred brands:" is a HEADING, and splitting on
# the substring "prefer" turned it into the brand "red brands" — so every real
# preference (Blue Bottle, Clif, LaCroix) was discarded and the cart was ranked by
# price alone.
_NOT_A_BRAND = frozenset(
    {
        "brand", "brands", "red brands", "preferred", "preferred brands",
        "available", "where available", "coffee", "snack bars", "snacks",
        "sparkling water", "water", "these", "them", "the following",
        "brand exists", "a preferred brand exists", "non-approved brands",
        "approved brands", "vendor", "vendors", "exists",
    }
)

# A rule that PROHIBITS something is not a preference list. Mining
# "Avoid non-approved brands where a preferred brand exists" for brand names
# yields the non-brand "brand exists"; worse, it would let a negative rule inject
# a fake preference that then RANKS products up. Preferences come only from
# affirmative rules.
_NEGATION_MARKERS = (
    "do not", "don't", "never", "avoid", "not allowed", "prohibited",
    "banned", "forbidden",
)


def _is_negative_rule(rule: str) -> bool:
    low = rule.lower()
    return any(marker in low for marker in _NEGATION_MARKERS)


def _preferred_brands(rules: list[str]) -> list[str]:
    """Pull candidate brand names out of free-text context rules.

    Handles both real phrasings:
      - inline prose: "Prefer Blue Bottle/Clif/LaCroix where available"
      - a bulleted list under a "Preferred brands:" heading, where each rule is
        its own line like "Blue Bottle for coffee"

    Best-effort by design: rules are prose, so an unparseable rule just means "no
    brand preference" rather than a wrong one. The word-boundary split on
    "prefer" matters — a substring split reads "Preferred" and yields a brand that
    does not exist.
    """
    brands: list[str] = []
    for rule in rules:
        low = rule.lower().strip()
        if not low:
            continue

        # A prohibition is never a preference list. Skipping these is what stops
        # "Avoid non-approved brands where a preferred brand exists" from being
        # read as the brand "brand exists".
        if _is_negative_rule(low):
            continue

        # Brand-per-line list items, in the two shapes the policy actually
        # renders: "Blue Bottle (coffee)" and "Blue Bottle for coffee". Take the
        # part before the category in each case.
        if "prefer" not in low:
            candidate = low
            if "(" in candidate:
                candidate = candidate.split("(", 1)[0]
            elif " for " in candidate:
                candidate = candidate.split(" for ", 1)[0]
            else:
                candidate = ""
            candidate = candidate.strip(" .;:*-")
            if candidate and candidate not in _NOT_A_BRAND and 2 <= len(candidate) <= 24:
                brands.append(candidate)
            continue

        if "prefer" not in low and "brand" not in low:
            continue
        # Split on the WORD, not the substring, so "Preferred" cannot leak its
        # tail ("red ...") into a brand name.
        parts = re.split(r"\bprefer(?:s|red|ring)?\b", low, maxsplit=1)
        segment = parts[1] if len(parts) > 1 else low
        for token in re.split(r"[\/,;]| and | or ", segment):
            token = token.strip(" .;:*-")
            token = re.sub(r"^(the|a|an)\s+", "", token)
            token = re.sub(r"\s+(for|when|where)\b.*$", "", token).strip()
            if token in _NOT_A_BRAND:
                continue
            if 2 <= len(token) <= 24 and re.search(r"[a-z]", token):
                brands.append(token)
    return brands


# The ways a policy author actually writes a prohibition. A rule is prose, so
# recognising only one shape is how a real run came to buy a banned item: the
# seeded Senso policy says "Do not purchase energy drinks", which the original
# `\bno\s+` pattern did not match at all — the rule silently excluded nothing
# while the approval screen implied the policy had been applied.
#
# Each pattern captures the banned PHRASE. Order matters only in that the first
# match wins, and all of them mean the same thing.
# Any auxiliary before the negation: "do not", "don't", "should not", "must not",
# "cannot", "won't", "never", "may not". Enumerating only do/don't/never is what
# let the live phrasing "you should not purchase energy drinks" through — the
# policy answer is LLM-written and varies this wording between responses, so the
# auxiliary is matched generically rather than listed.
_AUX = (
    r"(?:do|does|should|must|can|could|would|will|may|shall)\s*n[o']?t"
    r"|don'?t|cannot|can'?t|won'?t|shouldn'?t|mustn'?t|never"
)

_PROHIBITION_PATTERNS = (
    # "<aux> purchase/buy/order/stock X"
    rf"\b(?:{_AUX})\s+(?:purchase|buy|order|get|stock|include|add)\s+([a-z][a-z \-]{{2,60}})",
    # "avoid X"
    r"\bavoid\s+([a-z][a-z \-]{2,60})",
    # "no X" — the original shape, kept
    r"\bno\s+([a-z][a-z \-]{2,60})",
    # "X are not allowed / is prohibited / are banned"
    r"\b([a-z][a-z \-]{2,60}?)\s+(?:are|is)\s+(?:not\s+allowed|prohibited|banned|forbidden)",
)

# Words that carry no restrictive meaning on their own. Without this, a rule like
# "avoid non-approved brands" bans every product with "brand" in its text, and
# "do not purchase energy drinks" would ban an "Energy-free snack bar" — the
# opposite of a policy that explicitly allows snack bars.
_PROHIBITION_STOPWORDS = frozenset(
    {
        "the", "a", "an", "any", "all", "other", "others", "and", "or",
        "where", "when", "that", "which", "with", "from", "for", "than",
        "non", "approved", "brand", "brands", "item", "items", "product",
        "products", "thing", "things", "purchase", "purchases", "order",
        "orders", "buy", "buying", "stock", "please", "also", "etc",
    }
)


def _is_disallowed(name: str, brand: str, rules: list[str]) -> bool:
    """True when a negative policy rule prohibits this product.

    Matches the ordinary phrasings of a prohibition (see _PROHIBITION_PATTERNS),
    then requires a WHOLE-WORD hit against the product text. The whole-word check
    is what stops "energy drinks" from banning "Energy-free snack bars": a
    substring check reads `energy` inside `energy-free` and excludes a product the
    same policy explicitly allows.
    """
    hay = f"{name} {brand}".lower()
    # Tokenize the product once, so matching is word-level rather than substring.
    # Hyphens split too: "energy-free" -> {"energy", "free"} would re-introduce
    # the collision, so the hyphenated form is kept whole AND its parts are
    # dropped from consideration by only ever matching full tokens below.
    product_words = set(re.findall(r"[a-z]+(?:-[a-z]+)*", hay))

    for rule in rules:
        low = rule.lower()
        for pattern in _PROHIBITION_PATTERNS:
            m = re.search(pattern, low)
            if not m:
                continue
            banned_phrase = m.group(1).strip()
            # Singularise crudely ("drinks" -> "drink") so a plural rule still
            # matches a singular product name and vice versa.
            candidates = set()
            for word in re.findall(r"[a-z\-]+", banned_phrase):
                if len(word) < 4 or word in _PROHIBITION_STOPWORDS:
                    continue
                candidates.add(word)
                if word.endswith("s") and len(word) > 4:
                    candidates.add(word[:-1])
            if not candidates:
                continue
            # A hit needs a whole product token, so "energy" matches the token
            # "energy" but never the token "energy-free".
            for word in candidates:
                if word in product_words:
                    return True
                if f"{word}s" in product_words:
                    return True
    return False


def _select_items(
    products: list[dict], budget_cents: int, preferred: list[str], rules: list[str]
) -> list[tuple[str, int]]:
    """Choose (product_id, qty) pairs that respect the budget and prefer brands.

    Strategy: rank preferred-brand products first, add up to 2 of each while the
    running total stays within budget, then top up with other allowed products at
    qty 1. Never exceeds budget; always returns at least one item if any fits.
    Negative rules (e.g. "No energy drinks") drop matching products entirely.
    """
    def prefers(p: dict) -> bool:
        hay = f"{p['brand']} {p['name']}".lower()
        return any(b in hay for b in preferred)

    allowed = [
        p for p in products if not _is_disallowed(p["name"], p["brand"], rules)
    ]

    ranked = sorted(allowed, key=lambda p: (0 if prefers(p) else 1, p["price_cents"]))

    plan: dict[str, int] = {}
    total = 0
    # first pass: preferred brands, up to 2 each
    for p in ranked:
        if not prefers(p):
            continue
        for _ in range(2):
            if total + p["price_cents"] <= budget_cents:
                plan[p["id"]] = plan.get(p["id"], 0) + 1
                total += p["price_cents"]
    # second pass: any remaining allowed product, qty 1, to use budget sensibly
    for p in ranked:
        if p["id"] in plan:
            continue
        if total + p["price_cents"] <= budget_cents:
            plan[p["id"]] = 1
            total += p["price_cents"]

    # guarantee at least one item if the cheapest fits
    if not plan and ranked:
        cheapest = min(ranked, key=lambda p: p["price_cents"])
        if cheapest["price_cents"] <= budget_cents:
            plan[cheapest["id"]] = 1

    return [(pid, qty) for pid, qty in plan.items() if qty > 0]
