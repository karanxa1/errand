"""Voice ticket mint — the authenticated half of voice-relay auth.

/api/voice/ws cannot carry a bearer token (browsers cannot set headers on a
WebSocket), so authentication happens HERE, over ordinary authenticated HTTP,
and the socket presents the resulting one-shot ticket instead. See
app/voice/tickets.py for the ticket's properties and its single-process
constraint.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import get_current_user
from app.models import User
from app.voice.tickets import issue_ticket

router = APIRouter(prefix="/api/voice", tags=["voice"])


class VoiceTicketOut(BaseModel):
    ticket: str
    # Seconds, so the client can decide whether a stale ticket is worth trying.
    expires_in: int


@router.post("/ticket", response_model=VoiceTicketOut)
async def mint_voice_ticket(user: User = Depends(get_current_user)) -> VoiceTicketOut:
    """AUTH REQUIRED. The voice relay spends real money (Deepgram + OpenAI
    credits, and run_errand can reach checkout), so the socket's identity is
    fixed here from the verified bearer token and baked into the ticket. The
    client cannot claim to be anyone else later: the WS never sees a user id,
    only the opaque ticket."""
    ticket, expires_in = issue_ticket(user.id, user.email)
    return VoiceTicketOut(ticket=ticket, expires_in=expires_in)
