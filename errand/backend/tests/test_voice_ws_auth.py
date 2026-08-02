"""The relay half of voice auth: an unticketed socket must die at 4401 before
Deepgram is ever dialled.

Runs under pytest if installed, and standalone (`uv run python
tests/test_voice_ws_auth.py`) if not.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from app.main import app  # noqa: E402
from app.voice.tickets import clear_tickets, issue_ticket  # noqa: E402

client = TestClient(app)


class _DeepgramNeverDialled(Exception):
    """Raised by the stubbed websockets.connect. Reaching it at all is the
    failure the ticket check exists to prevent, so the tests below assert on
    WHETHER it was reached, not on what it returned."""


def _expect_close_code(url: str) -> int:
    """Open the relay WS and return the close code it hands back. TestClient's
    raw receive() surfaces the close as a message rather than an exception, so
    both shapes are handled."""
    try:
        with client.websocket_connect(url) as ws:
            message = ws.receive()
    except WebSocketDisconnect as exc:
        return exc.code
    if message.get("type") == "websocket.close":
        return int(message.get("code", 1000))
    raise AssertionError(f"{url} stayed open; expected an unauthorized close: {message}")


def test_mint_endpoint_requires_a_bearer_token() -> None:
    """The whole scheme rests on the mint being authenticated: if anyone could
    mint, the ticket would be a formality rather than a credential."""
    res = client.post("/api/voice/ticket")
    assert res.status_code == 401, res.text
    res = client.post("/api/voice/ticket", headers={"Authorization": "Bearer nonsense"})
    assert res.status_code == 401, res.text


def test_ws_without_ticket_is_closed_4401() -> None:
    clear_tickets()
    assert _expect_close_code("/api/voice/ws") == 4401


def test_ws_with_unknown_ticket_is_closed_4401() -> None:
    clear_tickets()
    assert _expect_close_code("/api/voice/ws?ticket=forged-by-hand") == 4401


def test_ws_ticket_cannot_be_replayed() -> None:
    clear_tickets()
    ticket, _ = issue_ticket("user-123", "buyer@example.com")

    # First use: accepted, and the relay proceeds to dial Deepgram — which is
    # stubbed out so the test never touches the network or spends credits.
    dialled: list[str] = []
    original_connect = websockets.connect

    async def _stub_connect(*args, **kwargs):  # type: ignore[no-untyped-def]
        dialled.append("deepgram")
        raise _DeepgramNeverDialled("stubbed")

    websockets.connect = _stub_connect  # type: ignore[assignment]
    try:
        with client.websocket_connect(f"/api/voice/ws?ticket={ticket}") as ws:
            frame = ws.receive_json()
        assert frame["type"] == "voice.error", frame
        assert dialled == ["deepgram"], "a valid ticket must let the relay run"

        # Second use of the SAME ticket: rejected before Deepgram is touched.
        dialled.clear()
        assert _expect_close_code(f"/api/voice/ws?ticket={ticket}") == 4401
        assert dialled == [], "a replayed ticket must never reach Deepgram"
    finally:
        websockets.connect = original_connect  # type: ignore[assignment]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{'FAILED' if failures else 'PASSED'} — {failures} failure(s)")
    sys.exit(1 if failures else 0)
