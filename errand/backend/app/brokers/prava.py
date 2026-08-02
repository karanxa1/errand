"""Prava PaymentBroker — verified against sandbox.api.prava.space.

Endpoints (see docs/api-reference.md):
  POST /v1/sessions
  GET  /v1/sessions/{id}/payment-result   (poll every 3s)
  POST /v1/sessions/{id}/report-status
Credential lives at transactions[0].line_items[0]. Secret key server-side only.
"""

from __future__ import annotations

import httpx

from app.contracts import (
    CreateSessionInput,
    CreateSessionResult,
    PaymentCredential,
    PollCompleted,
    PollCredentialResult,
    PollFailed,
    PollPending,
    TxnStatus,
)


class PravaPaymentBroker:
    def __init__(self, secret_key: str, api_base: str) -> None:
        if not secret_key.startswith("sk_"):
            raise ValueError("Prava secret key must start with sk_")
        self._secret = secret_key
        self._base = api_base.rstrip("/")
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
        body = {
            "user_id": data.user_id,
            "user_email": data.user_email,
            "total_amount": f"{data.total_cents / 100:.2f}",
            "currency": "USD",
            "description": "Errand agent purchase",
            "purchase_context": [
                {
                    "merchant_details": {
                        "name": data.merchant.name,
                        "url": data.merchant.url,
                        "country_code_iso2": "US",
                    },
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
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self._base}/v1/sessions", headers=self._headers(), json=body
            )
            r.raise_for_status()
            d = r.json()
        return CreateSessionResult(session_id=d["session_id"], iframe_url=d["iframe_url"])

    async def poll_credential(self, session_id: str) -> PollCredentialResult:
        async with httpx.AsyncClient(timeout=30) as client:
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
            r.raise_for_status()
            d = r.json()

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
        self, session_id: str, txn_ref_id: str, status: TxnStatus
    ) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self._base}/v1/sessions/{session_id}/report-status",
                headers=self._headers(),
                json={"txn_ref_id": txn_ref_id, "txn_status": status},
            )
            r.raise_for_status()
