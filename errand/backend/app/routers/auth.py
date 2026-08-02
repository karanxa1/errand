"""Auth routes: register, login, me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_token, get_current_user, hash_password, verify_password
from app.db import get_session
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str


class AuthResponse(BaseModel):
    token: str
    user: UserOut


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest, session: AsyncSession = Depends(get_session)
) -> AuthResponse:
    email = req.email.lower().strip()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )
    user = User(email=email, name=req.name.strip(), password_hash=hash_password(req.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return AuthResponse(
        token=create_token(user.id),
        user=UserOut(id=user.id, email=user.email, name=user.name),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    req: LoginRequest, session: AsyncSession = Depends(get_session)
) -> AuthResponse:
    email = req.email.lower().strip()
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    return AuthResponse(
        token=create_token(user.id),
        user=UserOut(id=user.id, email=user.email, name=user.name),
    )


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(id=user.id, email=user.email, name=user.name)
