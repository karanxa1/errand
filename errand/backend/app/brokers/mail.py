"""AgentMail MailBroker — verified against the installed `agentmail` SDK.

The agent reads its OWN inbox to close the loop after a purchase: it waits for
the merchant's order-confirmation email, parses the order id + total, and can
reply. See docs/api-reference.md (AgentMail section).

SDK surface used (confirmed in .venv/.../agentmail):
  AgentMail(api_key=...)                                  → sync client
  client.inboxes.create(request=CreateInboxRequest(...))  → Inbox(.inbox_id, .email)
  client.inboxes.messages.list(inbox_id, limit=...)       → ListMessagesResponse(.messages[])
       MessageItem: .message_id .thread_id .from_ (alias "from") .subject
                    .preview .timestamp (datetime) .labels .attachments
  client.inboxes.messages.get(inbox_id, message_id)       → Message(.text/.extracted_text)
  client.inboxes.messages.reply(inbox_id, message_id, text=...)
       ^ 0.5.8-specific keyword form; see the pin note at AgentMailBroker.reply.

The SDK is synchronous; every call is offloaded with `asyncio.to_thread` so the
broker methods stay non-blocking.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from agentmail import AgentMail
from agentmail.inboxes.types.create_inbox_request import CreateInboxRequest

from app.contracts import InboxMessage, OrderConfirmation

# Idempotent client_id: re-creating with the same id returns the same inbox.
_CLIENT_ID = "errand-agent-inbox"
_POLL_INTERVAL_S = 3.0
_CONFIRM_KEYWORDS = ("order", "confirmed", "confirmation", "receipt", "purchase")
_ORDER_ID_PATTERNS = (
    re.compile(r"\bORD-?\d+\b", re.IGNORECASE),
    re.compile(r"order\s*#?\s*([A-Za-z0-9][A-Za-z0-9-]{2,})", re.IGNORECASE),
)
_AMOUNT_RE = re.compile(r"\$\s?(\d[\d,]*)(?:\.(\d{2}))?")


class AgentMailBroker:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("AgentMail API key is required")
        self._client = AgentMail(api_key=api_key)
        self._inbox_id: str | None = None
        self._address: str | None = None

    # ── inbox lifecycle ───────────────────────────────────────────────────────

    async def ensure_inbox(self) -> str:
        if self._address is not None:
            return self._address
        inbox = await asyncio.to_thread(
            self._client.inboxes.create,
            request=CreateInboxRequest(
                client_id=_CLIENT_ID,
                display_name="Errand Agent",
            ),
        )
        # inbox_id, email and pod_id are three separate fields on the SDK's Inbox
        # and no reference states they hold the same value, so the two uses stay
        # separate: .email is what a merchant replies to and is what we hand out,
        # inbox_id is the API handle and is what every later call takes. The
        # fallback to inbox_id only covers an SDK build that omits .email; it is
        # a last resort for display, not a claim that the two are equal.
        self._inbox_id = inbox.inbox_id
        self._address = getattr(inbox, "email", None) or inbox.inbox_id
        return self._address

    # ── reading ───────────────────────────────────────────────────────────────

    async def list_messages(self, limit: int = 10) -> list[InboxMessage]:
        inbox_id = await self._require_inbox()
        resp = await asyncio.to_thread(
            self._client.inboxes.messages.list, inbox_id, limit=limit
        )
        return [_item_to_message(m) for m in (resp.messages or [])]

    async def wait_for_confirmation(
        self, merchant: str, since_iso: str, timeout_ms: int
    ) -> OrderConfirmation:
        inbox_id = await self._require_inbox()
        since = _parse_iso(since_iso)
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000.0

        while True:
            resp = await asyncio.to_thread(
                self._client.inboxes.messages.list, inbox_id, limit=20
            )
            for item in resp.messages or []:
                if not _after(item.timestamp, since):
                    continue
                subject = item.subject or ""
                preview = item.preview or ""
                if not _looks_like_confirmation(subject, preview):
                    continue
                # Cheap check passed → fetch full body to parse id + amount.
                full = await asyncio.to_thread(
                    self._client.inboxes.messages.get, inbox_id, item.message_id
                )
                msg = _message_to_message(full)
                corpus = f"{msg.subject}\n{msg.text}"
                if not _looks_like_confirmation(msg.subject, msg.text):
                    continue
                return OrderConfirmation(
                    matched=True,
                    order_id=_extract_order_id(corpus),
                    total_cents=_extract_total_cents(corpus),
                    merchant=merchant,
                    raw=msg,
                )

            if asyncio.get_event_loop().time() >= deadline:
                # Never raise on timeout — the caller decides what to do next.
                return OrderConfirmation(matched=False, merchant=merchant, raw=None)
            await asyncio.sleep(_POLL_INTERVAL_S)

    # ── writing ────────────────────────────────────────────────────────────────

    async def reply(self, message_id: str, text: str) -> None:
        inbox_id = await self._require_inbox()
        # VERSION-COUPLED CALL — read before bumping `agentmail`.
        # Installed 0.5.8 declares reply(inbox_id, message_id, *, text=..., ...),
        # i.e. the body flattened into keyword-only args, which is what this call
        # passes. The SDK's current published reference declares
        # reply(inbox_id, message_id, request: ReplyToMessageRequest, ...) — a
        # REQUIRED positional — so crossing that boundary turns this into a
        # TypeError at the one moment the agent tries to answer a merchant, and
        # the signature change is silent at install time. pyproject pins
        # agentmail to 0.5.8 for this reason; moving the pin means rewriting this
        # call to build and pass the request object.
        # https://raw.githubusercontent.com/agentmail-to/agentmail-python/main/reference.md
        await asyncio.to_thread(
            self._client.inboxes.messages.reply,
            inbox_id,
            message_id,
            text=text,
        )

    # ── internals ────────────────────────────────────────────────────────────

    async def _require_inbox(self) -> str:
        if self._inbox_id is None:
            await self.ensure_inbox()
        assert self._inbox_id is not None  # ensured above
        return self._inbox_id


# ── mapping helpers ─────────────────────────────────────────────────────────

def _item_to_message(item: object) -> InboxMessage:
    """Map a list `MessageItem` (preview only, no full body) to InboxMessage."""
    text = (
        getattr(item, "extracted_text", None)
        or getattr(item, "text", None)
        or getattr(item, "preview", None)
        or ""
    )
    return InboxMessage(
        id=getattr(item, "message_id", "") or "",
        from_addr=getattr(item, "from_", None) or "",
        subject=getattr(item, "subject", None) or "",
        text=text,
        received_at=_ts_to_iso(getattr(item, "timestamp", None)),
        attachments=_map_attachments(getattr(item, "attachments", None)),
    )


def _message_to_message(msg: object) -> InboxMessage:
    """Map a full `Message` (has extracted_text/text) to InboxMessage."""
    text = (
        getattr(msg, "extracted_text", None)
        or getattr(msg, "text", None)
        or getattr(msg, "preview", None)
        or ""
    )
    return InboxMessage(
        id=getattr(msg, "message_id", "") or "",
        from_addr=getattr(msg, "from_", None) or "",
        subject=getattr(msg, "subject", None) or "",
        text=text,
        received_at=_ts_to_iso(getattr(msg, "timestamp", None)),
        attachments=_map_attachments(getattr(msg, "attachments", None)),
    )


def _map_attachments(atts: object) -> list[dict]:
    if not atts:
        return []
    out: list[dict] = []
    for a in atts:  # type: ignore[union-attr]
        if hasattr(a, "model_dump"):
            out.append(a.model_dump(mode="json"))
        elif isinstance(a, dict):
            out.append(a)
        else:
            out.append({"value": str(a)})
    return out


# ── parsing / matching ──────────────────────────────────────────────────────

def _looks_like_confirmation(subject: str, body: str) -> bool:
    hay = f"{subject}\n{body}".lower()
    return any(kw in hay for kw in _CONFIRM_KEYWORDS)


def _extract_order_id(text: str) -> str | None:
    for pat in _ORDER_ID_PATTERNS:
        m = pat.search(text)
        if m:
            # Group 1 if the pattern captured a value, else the whole match.
            return (m.group(1) if m.lastindex else m.group(0)).strip()
    return None


def _extract_total_cents(text: str) -> int | None:
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    dollars = int(m.group(1).replace(",", ""))
    cents = int(m.group(2)) if m.group(2) else 0
    return dollars * 100 + cents


# ── time helpers ────────────────────────────────────────────────────────────

def _parse_iso(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt_ = datetime.fromisoformat(s)
    if dt_.tzinfo is None:
        dt_ = dt_.replace(tzinfo=timezone.utc)
    return dt_


def _after(ts: object, since: datetime) -> bool:
    if not isinstance(ts, datetime):
        return True  # unknown timestamp → don't exclude
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= since


def _ts_to_iso(ts: object) -> str:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    if isinstance(ts, str):
        return ts
    return datetime.now(timezone.utc).isoformat()
