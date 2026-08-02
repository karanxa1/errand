"""Prava wallet shopper — REAL merchants, via Prava's own UCP catalog.

This is the ShopperBroker the demo storefront was standing in for. Instead of
driving Playwright over a storefront we control, it drives Prava's wallet API,
which indexes real merchants and runs their checkout on its own browser harness:

    POST /v1/wallet/shop/search    → products across merchants
    POST /v1/wallet/shop/product   → a product's variants, per selling merchant
    POST /v1/wallet/shop/quote     → the exact total (item + shipping + tax)
    POST /v1/wallet/shop/checkout  → pay it with the Prava-issued card

It implements the same two-method Protocol as the browser shopper, so the
orchestrator does not change: build a cart, park on checkout, and only spend
after the human approves.

THREE THINGS THAT ARE NOT NEGOTIABLE HERE, because each one is a way to spend
someone's money wrongly:

  * The merchant is PINNED. `build_cart` searches only the domain the policy
    approved, and then keeps only the variants offered BY that domain — the same
    product is usually listed by several sellers, and `product` returns all of
    them. Shopping a different seller would also break the payment: the card
    session `run_errand` mints is scoped to the policy merchant, so a card
    presented at anyone else declines at the network.

  * The quote is the price. A Prava card is minted for the quoted total and is
    scoped to it, so if a stale quote has to be refreshed after approval, the new
    total must equal the approved one exactly. If it moved, this refuses rather
    than charging a number nobody approved.

  * A timed-out checkout is never retried. The charge may already have gone
    through; the wallet's own replay of a terminal result is reported as such,
    never as a fresh purchase.

Delivery address and contact details live in the user's wallet and are injected
server-side at quote time — they never pass through this process.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.brokers.policy import _is_disallowed, _preferred_brands
from app.contracts import (
    CartItem,
    CartResult,
    CheckoutState,
    Merchant,
    OrderResult,
    PaymentCredential,
    PurchaseContext,
)
from app.prava.wallet import BROWSER_TIMEOUT_S, WalletClient, WalletError

logger = logging.getLogger(__name__)

# How many search hits to open before giving up on finding an orderable variant.
# Each `product` call is a network round-trip, not a browser run, so this is
# cheap; the bound exists so a bad query cannot walk a whole catalog.
MAX_PRODUCTS_INSPECTED = 6

# How many variants we are willing to QUOTE. Each quote spins a real browser on
# the merchant (15-30s), so this is the expensive bound. Two attempts covers
# "the cheapest one's shipping pushed it over budget, try the next".
MAX_QUOTE_ATTEMPTS = 2

# Wallet error codes that mean "this quote is stale, get another one".
_EXPIRED_CODES = frozenset({"SHOP_SESSION_EXPIRED", "SHOP_QUOTE_EXPIRED"})

# Words that describe the ASK rather than the PRODUCT. Left in the keyword query
# they pull the catalog toward stopwords; the user's full sentence still travels
# as `intent`, which is what UCP ranks on, so nothing is lost by trimming them.
_INTENT_VERBS = frozenset(
    {
        "order", "orders", "buy", "get", "grab", "restock", "reorder", "purchase",
        "please", "for", "us", "me", "our", "the", "a", "an", "some", "we", "need",
        "want", "would", "like", "to", "and", "with", "of",
    }
)


class PravaShopError(RuntimeError):
    """A wallet shopping step failed, in words an operator can act on."""

    def __init__(self, step: str, message: str, *, code: str | None = None) -> None:
        self.step = step
        self.code = code
        super().__init__(f"[prava-shop:{step}] {message}")


@dataclass(frozen=True)
class _QuotedVariant:
    """What a quote was for — enough to ask for the same thing again."""

    variant_id: str
    merchant_domain: str
    quantity: int
    total_cents: int


def merchant_domain(merchant_url: str) -> str:
    """The bare host the wallet indexes merchants by (no scheme, no `www.`)."""
    host = (urlparse(merchant_url).hostname or merchant_url).lower().strip()
    return host[4:] if host.startswith("www.") else host


def search_query(intent: str, *, max_words: int = 8) -> str:
    """Tighten a natural-language errand into product keywords.

    Best-effort by design: the user's full sentence is sent alongside as
    `intent`, which is the field UCP actually ranks on, so a clumsy trim costs
    ranking quality and never correctness. Budget clauses ("under $200") are
    dropped because a price is not a product word.
    """
    text = re.sub(r"\bunder\s*\$?\s*[\d,.]+k?\b", " ", intent, flags=re.IGNORECASE)
    text = re.sub(r"[$€£]\s*[\d,.]+", " ", text)
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", text)]
    kept = [w for w in words if w.lower() not in _INTENT_VERBS]
    chosen = (kept or words)[:max_words]
    return " ".join(chosen) if chosen else intent.strip()[:120]


def _cents(value: object) -> int | None:
    """Read a cents integer from the wallet's several numeric spellings."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str):
        try:
            return int(round(float(value)))
        except ValueError:
            return None
    return None


def _amount_to_cents(amount: object) -> int | None:
    """Read a decimal money string like "27.98" as cents."""
    if isinstance(amount, (int, float)) and not isinstance(amount, bool):
        return int(round(float(amount) * 100))
    if isinstance(amount, str):
        try:
            return int(round(float(amount.replace(",", "").strip()) * 100))
        except ValueError:
            return None
    return None


class PravaShopBroker:
    """ShopperBroker over the Prava wallet's real-merchant catalog."""

    def __init__(self, client: WalletClient, *, ships_to: str = "US") -> None:
        self._client = client
        self._ships_to = ships_to
        # checkout_session_id → what it was a quote FOR. A quote can lapse while
        # the human is at the approval gate, and refreshing it needs the variant,
        # the merchant, the quantity, and the total that was approved. Bounded by
        # construction: a broker is built per run and a run quotes at most
        # MAX_QUOTE_ATTEMPTS times.
        self._quoted: dict[str, _QuotedVariant] = {}

    # ── ShopperBroker Protocol ────────────────────────────────────────────────

    async def build_cart(
        self, merchant_url: str, intent: str, context: PurchaseContext
    ) -> CartResult:
        domain = merchant_domain(merchant_url)
        if not domain:
            raise PravaShopError("build_cart", f"{merchant_url!r} has no usable host.")

        results = await self._search(intent, domain)
        if not results:
            raise PravaShopError(
                "build_cart",
                f"Prava's catalog returned nothing for {search_query(intent)!r} at "
                f"{domain}. The policy approves that merchant, so this stops rather "
                f"than buying somewhere the policy did not name.",
            )

        candidates = await self._rank_variants(results, domain, context)
        if not candidates:
            raise PravaShopError(
                "build_cart",
                f"No orderable variant at {domain} fits the policy "
                f"(budget ${context.budget_cents / 100:.2f}, {len(context.rules)} rules).",
            )

        best: tuple[CartResult, int] | None = None
        failures: list[str] = []
        for variant in candidates[:MAX_QUOTE_ATTEMPTS]:
            try:
                cart = await self._quote(variant, domain, context)
            except PravaShopError as exc:
                failures.append(str(exc))
                continue
            if cart.total_cents <= context.budget_cents:
                return cart
            logger.info(
                "[prava-shop] quote %s over budget (%s > %s); trying the next variant",
                cart.checkout.session_ref,
                cart.total_cents,
                context.budget_cents,
            )
            if best is None or cart.total_cents < best[1]:
                best = (cart, cart.total_cents)

        if best is not None:
            # Over budget, but real. Handing it back lets the orchestrator abort
            # with the honest reason and the actual number on the audit trail.
            return best[0]
        raise PravaShopError(
            "build_cart", "Could not price any candidate: " + "; ".join(failures[:2])
        )

    async def complete_checkout(
        self, checkout: CheckoutState, credential: PaymentCredential
    ) -> OrderResult:
        session_id = checkout.session_ref
        if not session_id:
            raise PravaShopError(
                "complete_checkout", "No checkout session id on the parked cart."
            )
        approved_cents = sum(i.qty * i.price_cents for i in checkout.items)

        try:
            return await self._pay(session_id, credential)
        except PravaShopError as exc:
            if exc.code not in _EXPIRED_CODES:
                raise
            memo = self._quoted.get(session_id)
            if memo is None:
                raise PravaShopError(
                    "complete_checkout",
                    "The quote expired and there is nothing on file to re-price it "
                    "with. Re-run the errand.",
                    code=exc.code,
                ) from exc

            logger.info("[prava-shop] quote %s expired; re-pricing", session_id)
            refreshed = await self._quote_raw(memo.variant_id, memo.merchant_domain, memo.quantity)
            if refreshed.total_cents != approved_cents:
                # The card is scoped to the amount the human approved. Paying a
                # different number is either a decline at the network or, worse,
                # a charge nobody agreed to.
                raise PravaShopError(
                    "complete_checkout",
                    f"The price moved while waiting for approval "
                    f"(${approved_cents / 100:.2f} → ${refreshed.total_cents / 100:.2f}). "
                    f"Not charging a total nobody approved.",
                    code="PRICE_MOVED",
                ) from exc
            return await self._pay(refreshed.session_id, credential)

    async def discover_merchants(
        self, intent: str, context: PurchaseContext, limit: int = 3
    ) -> list[Merchant]:
        """Merchants that appear to stock this, when the approved ones do not.

        The LAST resort, and the orchestrator decides whether it is allowed at
        all (see Settings.merchant_discovery) — this method only answers "who
        has it". Same policy filter as `_rank_variants`: an offer whose price
        blows the budget, or whose text a rule prohibits, is not a merchant we
        would buy from, so it does not get suggested either.

        Returned in catalog rank order, cheapest offer first within a merchant,
        de-duplicated by domain.
        """
        try:
            data = await self._client.post(
                "/v1/wallet/shop/search",
                {
                    "query": search_query(intent),
                    "intent": intent[:500],
                    "limit": MAX_PRODUCTS_INSPECTED,
                    "shipsTo": self._ships_to,
                },
            )
        except WalletError as exc:
            logger.info("[prava-shop] merchant discovery failed: %s", exc)
            return []

        results = data.get("results")
        if not isinstance(results, list):
            return []

        cheapest: dict[str, int] = {}
        for result in results:
            if not isinstance(result, dict):
                continue
            domain = str(result.get("merchant") or "").lower().strip()
            if not domain:
                continue
            title = str(result.get("title") or "")
            if _is_disallowed(title, domain, context.rules):
                continue
            estimate = result.get("price_estimate")
            price = _amount_to_cents(
                estimate.get("amount") if isinstance(estimate, dict) else None
            )
            # An unpriced hit is still a lead — the quote is the real price
            # anyway — but a hit we KNOW blows the budget is not.
            if price is not None and price > context.budget_cents:
                continue
            best = cheapest.get(domain)
            if best is None or (price is not None and price < best):
                cheapest[domain] = price if price is not None else best or 0

        ordered = sorted(cheapest.items(), key=lambda kv: kv[1])
        return [Merchant(name=domain, url=f"https://{domain}") for domain, _ in ordered[:limit]]

    # ── steps ─────────────────────────────────────────────────────────────────

    async def _search(self, intent: str, domain: str) -> list[dict]:
        body = {
            "query": search_query(intent),
            "intent": intent[:500],
            "limit": MAX_PRODUCTS_INSPECTED,
            "merchantDomain": domain,
            "shipsTo": self._ships_to,
        }
        try:
            data = await self._client.post("/v1/wallet/shop/search", body)
        except WalletError as exc:
            raise PravaShopError("build_cart", str(exc), code=exc.code) from exc
        results = data.get("results")
        return [r for r in results if isinstance(r, dict)] if isinstance(results, list) else []

    async def _rank_variants(
        self, results: list[dict], domain: str, context: PurchaseContext
    ) -> list[dict]:
        """Open each hit and return its policy-allowed variants, best first.

        Ordering matches the browser shopper's: a preferred brand outranks a
        cheaper one, and price breaks the tie. Only variants sold BY the pinned
        merchant survive — `product` lists every seller of the same item.
        """
        preferred = _preferred_brands(context.rules)
        allowed: list[dict] = []

        for result in results[:MAX_PRODUCTS_INSPECTED]:
            product_id = result.get("product_id")
            if not isinstance(product_id, str) or not product_id:
                continue
            title = str(result.get("title") or "")
            try:
                data = await self._client.post(
                    "/v1/wallet/shop/product",
                    {"product_id": product_id, "merchantDomain": domain},
                )
            except WalletError as exc:
                logger.info("[prava-shop] product %s unreadable: %s", product_id, exc)
                continue

            product = data.get("product")
            if not isinstance(product, dict):
                continue
            variants = product.get("variants")
            if not isinstance(variants, list):
                continue

            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                variant_id = variant.get("id")
                if not isinstance(variant_id, str) or not variant_id:
                    continue
                # `available: false` from UCP can also mean "unknown", but an
                # unorderable listing wastes the one expensive step we have, so
                # it is dropped rather than merely sunk.
                if variant.get("available") is False:
                    continue
                if str(variant.get("merchantDomain") or domain).lower() != domain:
                    continue
                price = _cents(variant.get("priceAmount"))
                if price is None or price <= 0 or price > context.budget_cents:
                    continue
                label = str(variant.get("label") or title)
                if _is_disallowed(f"{title} {label}", domain, context.rules):
                    continue
                allowed.append(
                    {
                        "variant_id": variant_id,
                        "title": title or label,
                        "label": label,
                        "price_cents": price,
                        "preferred": any(
                            brand in f"{title} {label}".lower() for brand in preferred
                        ),
                    }
                )

        allowed.sort(key=lambda v: (0 if v["preferred"] else 1, v["price_cents"]))
        return allowed

    @dataclass(frozen=True)
    class _Quote:
        session_id: str
        total_cents: int
        subtotal_cents: int | None
        shipping_cents: int | None
        tax_cents: int | None

    async def _quote_raw(
        self, variant_id: str, domain: str, quantity: int
    ) -> "PravaShopBroker._Quote":
        body = {
            "variant_id": variant_id,
            "merchantDomain": domain,
            "quantity": quantity,
        }
        try:
            data = await self._client.post(
                "/v1/wallet/shop/quote", body, timeout_s=BROWSER_TIMEOUT_S
            )
        except WalletError as exc:
            raise PravaShopError("build_cart", str(exc), code=exc.code) from exc

        session_id = data.get("checkout_session_id")
        if not isinstance(session_id, str) or not session_id:
            raise PravaShopError("build_cart", "The quote carried no checkout session id.")
        final_price = data.get("final_price")
        total = _amount_to_cents(
            final_price.get("amount") if isinstance(final_price, dict) else None
        )
        breakdown = data.get("price_breakdown") if isinstance(data.get("price_breakdown"), dict) else {}
        subtotal = _cents(breakdown.get("subtotal_cents"))
        shipping = _cents(breakdown.get("shipping_cents"))
        tax = _cents(breakdown.get("tax_cents"))
        if total is None:
            parts = [p for p in (subtotal, shipping, tax) if p is not None]
            total = sum(parts) if parts else None
        if total is None or total <= 0:
            raise PravaShopError("build_cart", "The quote carried no usable total.")

        quote = self._Quote(
            session_id=session_id,
            total_cents=total,
            subtotal_cents=subtotal,
            shipping_cents=shipping,
            tax_cents=tax,
        )
        self._quoted[session_id] = _QuotedVariant(
            variant_id=variant_id,
            merchant_domain=domain,
            quantity=quantity,
            total_cents=total,
        )
        return quote

    async def _quote(
        self, variant: dict, domain: str, context: PurchaseContext
    ) -> CartResult:
        quote = await self._quote_raw(variant["variant_id"], domain, 1)
        items = _line_items(
            name=variant["title"] or variant["label"],
            total_cents=quote.total_cents,
            subtotal_cents=quote.subtotal_cents,
            shipping_cents=quote.shipping_cents,
            tax_cents=quote.tax_cents,
        )
        merchant_url = f"https://{domain}"
        return CartResult(
            items=items,
            total_cents=quote.total_cents,
            checkout=CheckoutState(
                merchant_url=merchant_url, items=items, session_ref=quote.session_id
            ),
        )

    async def _pay(self, session_id: str, credential: PaymentCredential) -> OrderResult:
        body = {
            "checkout_session_id": session_id,
            "credentials": {
                "token": credential.token,
                "cryptogram": credential.dynamic_cvv,
                "expiry_month": credential.expiry_month,
                "expiry_year": credential.expiry_year,
            },
        }
        try:
            # No retries. A timed-out charge may have landed; re-sending it is how
            # a double charge happens.
            data = await self._client.post(
                "/v1/wallet/shop/checkout", body, timeout_s=BROWSER_TIMEOUT_S
            )
        except WalletError as exc:
            replay = " (already processed — no new charge)" if exc.replayed else ""
            raise PravaShopError(
                "complete_checkout", f"{exc}{replay}", code=exc.code
            ) from exc

        status = str(data.get("status") or "").lower()
        if status != "paid":
            reason = data.get("failure_reason") or f"checkout {status or 'failed'}"
            raise PravaShopError("complete_checkout", str(reason), code="NOT_PAID")

        order_id = data.get("order_id")
        amount = data.get("amount") if isinstance(data.get("amount"), dict) else {}
        paid = amount.get("amount")
        currency = amount.get("currency") or "USD"
        confirmation = (
            f"Paid {paid} {currency}".strip() if paid else "Paid at the merchant"
        )
        if order_id:
            confirmation = f"{confirmation} — order {order_id}"
        return OrderResult(
            order_id=str(order_id) if order_id else session_id,
            confirmation_text=confirmation,
        )


def _line_items(
    *,
    name: str,
    total_cents: int,
    subtotal_cents: int | None,
    shipping_cents: int | None,
    tax_cents: int | None,
) -> list[CartItem]:
    """Break a quote into rows the approval screen can show.

    The rows MUST add up to the total: the approval screen is what the human
    reads before authorising a spend, and a set of numbers that does not sum to
    the amount being charged is a lie by arithmetic. When the breakdown does not
    reconcile — a missing field, a merchant discount, a rounding difference — the
    cart collapses to one honest row at the real total instead.
    """
    rows: list[CartItem] = []
    if subtotal_cents:
        rows.append(CartItem(name=name[:120] or "Item", qty=1, price_cents=subtotal_cents))
    if shipping_cents:
        rows.append(CartItem(name="Shipping", qty=1, price_cents=shipping_cents))
    if tax_cents:
        rows.append(CartItem(name="Tax", qty=1, price_cents=tax_cents))
    if rows and sum(r.qty * r.price_cents for r in rows) == total_cents:
        return rows
    return [CartItem(name=name[:120] or "Item", qty=1, price_cents=total_cents)]
