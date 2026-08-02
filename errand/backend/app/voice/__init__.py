"""Voice package — Deepgram Voice Agent relay + tool bridge.

The backend holds the Deepgram Voice Agent WebSocket (browser tokens are
FORBIDDEN on this key) and relays audio/events to the browser over our own WS.
See `relay.py` and docs/api-reference.md "Errand Voice Relay + Tool Bridge".
"""
