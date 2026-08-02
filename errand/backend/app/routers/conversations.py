"""Conversation + message routes, all scoped to the authenticated user."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_session
from app.models import Conversation, Message, User

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationOut(BaseModel):
    id: str
    title: str
    profile: str
    model: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    events: list | None = None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class CreateConversationRequest(BaseModel):
    title: str = Field(default="New chat", max_length=200)
    profile: str = "business"
    model: str = "sol"


class UpdateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    profile: str | None = None
    model: str | None = None


async def _owned(session: AsyncSession, user: User, conversation_id: str) -> Conversation:
    convo = await session.get(Conversation, conversation_id)
    if convo is None or convo.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return convo


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> list[Conversation]:
    rows = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(rows)


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    req: CreateConversationRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    convo = Conversation(
        user_id=user.id, title=req.title, profile=req.profile, model=req.model
    )
    session.add(convo)
    await session.commit()
    await session.refresh(convo)
    return convo


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationDetail:
    convo = await _owned(session, user, conversation_id)
    msgs = await session.scalars(
        select(Message)
        .where(Message.conversation_id == convo.id)
        .order_by(Message.created_at)
    )
    return ConversationDetail(
        id=convo.id,
        title=convo.title,
        profile=convo.profile,
        model=convo.model,
        created_at=convo.created_at,
        updated_at=convo.updated_at,
        messages=[
            MessageOut(
                id=m.id, role=m.role, content=m.content, events=m.events, created_at=m.created_at
            )
            for m in msgs
        ],
    )


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: str,
    req: UpdateConversationRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    convo = await _owned(session, user, conversation_id)
    if req.title is not None:
        convo.title = req.title
    if req.profile is not None:
        convo.profile = req.profile
    if req.model is not None:
        convo.model = req.model
    await session.commit()
    await session.refresh(convo)
    return convo


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    convo = await _owned(session, user, conversation_id)
    await session.delete(convo)
    await session.commit()
