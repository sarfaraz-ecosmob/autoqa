"""Auth endpoints (spec §25): register, login, refresh, me."""
import jwt as pyjwt
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.api.deps import authenticate_user, get_current_user
from app.audit import record as audit_record
from app.db import get_db
from app.models import Role, User
from app.security.passwords import hash_password
from app.security.ratelimit import RateLimiter
from app.security.tokens import (
    create_access_token,
    create_refresh_token,
    decode_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
_limiter = RateLimiter(limit=20, window_seconds=60)


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(default="", max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str


def _issue_tokens(user: User) -> dict:
    return {
        "access_token": create_access_token(user.id, user.role.value),
        "refresh_token": create_refresh_token(user.id),
        "token_type": "bearer",
    }


@router.post("/register", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)) -> dict:
    if db.execute(sa.select(User).where(User.email == body.email)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")
    first_user = not db.execute(sa.select(sa.func.count()).select_from(User)).scalar_one()
    user = User(
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        role=Role.admin if first_user else Role.tester,
    )
    db.add(user)
    db.commit()
    audit_record("auth.register", user.id, "user", user.id)
    return {"id": user.id, "email": user.email, "role": user.role.value}


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)) -> dict:
    if not _limiter.check(f"login:{body.email}"):
        raise HTTPException(status_code=429, detail="Too many attempts")
    user = authenticate_user(db, body.email, body.password)
    if user is None:
        audit_record("auth.login_failed", None, "user", body.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    audit_record("auth.login", user.id, "user", user.id)
    return _issue_tokens(user)


@router.post("/refresh")
def refresh(body: RefreshIn, db: Session = Depends(get_db)) -> dict:
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid refresh token") from exc
    user = db.execute(sa.select(User).where(User.id == payload["sub"])).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    return _issue_tokens(user)


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"id": user.id, "email": user.email, "role": user.role.value, "name": user.name}
