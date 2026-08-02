"""Prava MERCHANT PaymentBroker — verified against sandbox.api.prava.space.

This is the side of Prava where WE are the merchant of record for the session:
the user enters a card in Prava's PCI-compliant iframe, and Prava hands back a
one-time Visa network token scoped to the merchant, product and amount the user
saw. It has no catalog — buying happens elsewhere (the storefront shopper in
sandbox, `app.brokers.prava_shop` against real merchants in production).

Endpoints (https://docs.prava.space, and errand/docs/api-reference.md):
  POST /v1/sessions                            → session_id + iframe_url (201)
  GET  /v1/sessions/{id}/payment-result        → credential, poll every 3s
  POST /v1/sessions/{id}/report-status         → MANDATORY outcome to Visa
  POST /v1/sessions/{id}/revoke                → drop an abandoned session
  GET  /v1/listCards?customer_id=…             → cards already on file
  GET  /health                                 → connectivity check

Credential lives at transactions[0].line_items[0]. Secret key server-side only.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.contracts import (
    CreateSessionInput,
    CreateSessionResult,
    PaymentCredential,
    PollCompleted,
    PollCredentialResult,
    PollFailed,
    PollPending,
    SavedCard,
    TxnStatus,
)

# Every Prava error body is `{"error": {"code": ..., "message": ...}}`. Reading
# it beats `raise_for_status()`, whose message is the URL and a status number:
# AUTH_1001 ("Invalid API key") and VAL_2001 ("Invalid request body", with
# per-field detail) are different problems with different fixes, and a bare
# "401 Client Error" makes the operator guess which.
_TIMEOUT_S = 30


class PravaApiError(RuntimeError):
    """A Prava merchant-API call that failed, carrying the code it gave us."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        self.code = code
        self.status = status
        super().__init__(f"Prava {code}: {message}" if code else message)


def _raise_for_error(response: httpx.Response) -> dict[str, Any]:
    """Return the parsed body, or raise PravaApiError with Prava's own words."""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        error = payload.get("error") if isinstance(payload, dict) else None
        code = ""
        message = f"HTTP {response.status_code}"
        if isinstance(error, dict):
            code = str(error.get("code") or "")
            message = str(error.get("message") or message)
            details = error.get("details")
            # VAL_2001 carries fieldErrors; naming the field turns "invalid
            # request body" into something actionable.
            if isinstance(details, dict):
                field_errors = details.get("fieldErrors")
                if isinstance(field_errors, dict) and field_errors:
                    fields = ", ".join(
                        f"{k}: {'; '.join(str(m) for m in v)}"
                        if isinstance(v, list)
                        else f"{k}: {v}"
                        for k, v in field_errors.items()
                    )
                    message = f"{message} ({fields})"
        elif isinstance(error, str):
            message = error
        raise PravaApiError(code, message, status=response.status_code)
    return payload if isinstance(payload, dict) else {}


class PravaPaymentBroker:
    def __init__(
        self,
        secret_key: str,
        api_base: str,
        *,
        callback_url: str = "",
        user_country: str = "US",
        merchant_category_code: str = "",
        merchant_category: str = "",
    ) -> None:
        if not secret_key.startswith("sk_"):
            raise ValueError("Prava secret key must start with sk_")
        self._secret = secret_key
        self._base = api_base.rstrip("/")
        self._callback_url = callback_url
        self._user_country = user_country
        self._category_code = merchant_category_code
        self._category = merchant_category
        # Session ids this broker has read back a 200 for. Read by the 404 branch
        # in poll_credential to tell a misconfiguration apart from a blip.
        # Bounded by construction: build_brokers() makes a fresh broker per run
        # and a run polls exactly one session.
        self._seen_ok: set[str] = set()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._secret}",
            "Content-Type": "application/json",
        }

    async def create_session(self, data: CreateSessionInput) -> CreateSessionResult:
        merchant_details: dict[str, Any] = {
            "name": data.merchant.name,
            "url": data.merchant.url,
            "country_code_iso2": "US",
        }
        # MCC scopes the token at the network, so it is sent only when the
        # operator configured a real one rather than guessed here.
        if self._category_code:
            merchant_details["category_code"] = self._category_code
        if self._category:
            merchant_details["category"] = self._category

        body: dict[str, Any] = {
            "user_id": data.user_id,
            "user_email": data.user_email,
            "total_amount": f"{data.total_cents / 100:.2f}",
            "currency": "USD",
            "description": "Errand agent purchase",
            "purchase_context": [
                {
                    "merchant_details": merchant_details,
                    "product_details": [
                        {
                            "description": it.name,
                            "unit_price": f"{it.price_cents / 100:.2f}",
                            "quantity": it.qty,
                        }
                        for it in data.items
                    ],
                    "effective_until_minutes": 15,
                }
            ],
        }
        if self._user_country:
            body["user_country_code_iso2"] = self._user_country
        if data.external_order_ref:
            body["external_order_ref"] = data.external_order_ref[:255]
        # Must be HTTPS per the API; an http:// dev URL is dropped rather than
        # sent, since a rejected optional field would fail the whole session.
        if self._callback_url.startswith("https://"):
            body["callback_url"] = self._callback_url[:2048]

        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.post(
                f"{self._base}/v1/sessions", headers=self._headers(), json=body
            )
            d = _raise_for_error(r)
        return CreateSessionResult(
            session_id=d["session_id"],
            iframe_url=d["iframe_url"],
            session_token=d.get("session_token"),
            order_id=d.get("order_id"),
            expires_at=d.get("expires_at"),
        )

    async def poll_credential(self, session_id: str) -> PollCredentialResult:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.get(
                f"{self._base}/v1/sessions/{session_id}/payment-result",
                headers=self._headers(),
            )
            if r.status_code == 404:
                # Upstream defines this 404 as "Session not found or doesn't
                # belong to your merchant account" — a statement about who the
                # secret key belongs to, which does not change while a run is in
                # flight. Reading it as "pending" spends the orchestrator's whole
                # DEFAULT_CREDENTIAL_WAIT_S window and then reports "Payment
                # credential timed out", which points the operator at Prava when
                # the actual cause is our own key or a session opened under a
                # different merchant account. So: the first 404 for a session is
                # terminal, and a 404 is only absorbed once the session has
                # already answered 200 at least once, where a momentary upstream
                # read-inconsistency is the likelier reading than a session that
                # stopped existing — and the wall-clock still bounds that case.
                # The message is fixed text, never the upstream body: run_errand
                # copies res.message straight into the client-facing `reason`.
                # https://docs.prava.space/api-reference/get-payment-result
                if session_id in self._seen_ok:
                    return PollPending()
                return PollFailed(
                    code="SESSION_NOT_FOUND",
                    message=(
                        "Payment session is not reachable with the configured "
                        "merchant credentials."
                    ),
                )
            d = _raise_for_error(r)

        self._seen_ok.add(session_id)
        status = d.get("status")
        txns = d.get("transactions") or []
        # Which states can carry the card. The upstream reference documents
        # `token` and `dynamic_cvv` as "Only present when status is
        # awaiting_result", and its own 200 example returns the credential with
        # status "awaiting_result" — so keying on "completed" alone would poll
        # past the one state that has the card and time the errand out with no
        # 4xx anywhere to explain it. "completed" stays in the set as a safe
        # superset: the null-guard below already covers a state that does not
        # carry the fields, so accepting both cannot regress.
        # https://docs.prava.space/api-reference/get-payment-result
        if status in ("awaiting_result", "completed"):
            li = (txns[0].get("line_items") or [{}])[0] if txns else {}
            if not (li.get("token") and li.get("dynamic_cvv")):
                return PollPending()  # credential not materialised yet
            return PollCompleted(
                credential=PaymentCredential(
                    token=li["token"],
                    dynamic_cvv=li["dynamic_cvv"],
                    expiry_month=li["expiry_month"],
                    expiry_year=li["expiry_year"],
                    txn_ref_id=li["txn_ref_id"],
                )
            )
        if status == "failed":
            err = (txns[0].get("error") if txns else None) or {
                "code": "UNKNOWN",
                "message": "Payment failed",
            }
            return PollFailed(code=err.get("code", "UNKNOWN"), message=err.get("message", ""))
        return PollPending()

    async def report_status(
        self,
        session_id: str,
        txn_ref_id: str,
        status: TxnStatus,
        *,
        authorization_code: str | None = None,
        amount_paid: str | None = None,
        product_statuses: list[dict[str, Any]] | None = None,
    ) -> None:
        """Tell Prava the outcome so it can confirm to Visa.

        Mandatory once a credential exists: an unreported transaction sits in
        `awaiting_result` forever. The optional fields are the processor detail
        Visa's Confirmations API accepts when we have it; the errand path does
        not have an authorization code (the merchant's own checkout holds it), so
        they stay unset there and are here for callers that do.
        """
        body: dict[str, Any] = {"txn_ref_id": txn_ref_id, "txn_status": status}
        if authorization_code:
            body["authorization_code"] = authorization_code[:128]
        if amount_paid:
            body["amount_paid"] = amount_paid
        if product_statuses:
            body["product_statuses"] = product_statuses
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.post(
                f"{self._base}/v1/sessions/{session_id}/report-status",
                headers=self._headers(),
                json=body,
            )
            _raise_for_error(r)

    async def revoke_session(self, session_id: str) -> None:
        """Drop a session that will never be completed (abort, logout, timeout).

        Best-effort by nature — it runs on paths that are already unwinding — so
        callers should not let a failure here mask the reason they were aborting.
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            # `json={}` rather than no body: _headers() sets
            # Content-Type: application/json, and the server rejects that with an
            # empty payload (FST_ERR_CTP_EMPTY_JSON_BODY). Verified live.
            r = await client.post(
                f"{self._base}/v1/sessions/{session_id}/revoke",
                headers=self._headers(),
                json={},
            )
            _raise_for_error(r)

    async def list_cards(self, customer_id: str, *, status: str = "active") -> list[SavedCard]:
        """Cards already on file for a customer.

        `customer_id` is the same `user_id` used when creating sessions. A card
        found here can be pre-selected on a later session, which skips card entry
        entirely — the difference between a two-step approval and a one-tap one.
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.get(
                f"{self._base}/v1/listCards",
                headers=self._headers(),
                params={"customer_id": customer_id, "status": status},
            )
            d = _raise_for_error(r)
        cards = d.get("cards")
        if not isinstance(cards, list):
            return []
        parsed: list[SavedCard] = []
        for card in cards:
            if not isinstance(card, dict) or not card.get("card_id"):
                continue
            parsed.append(
                SavedCard(
                    card_id=str(card["card_id"]),
                    card_last4=str(card.get("card_last4") or ""),
                    card_brand=str(card.get("card_brand") or ""),
                    card_exp_month=card.get("card_exp_month"),
                    card_exp_year=card.get("card_exp_year"),
                    status=str(card.get("status") or "active"),
                )
            )
        return parsed

    async def health(self) -> bool:
        """Is the Prava backend answering? Unauthenticated; never raises."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{self._base}/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False
