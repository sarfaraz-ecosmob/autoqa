"""JWT creation/validation (spec §25 authentication)."""
import time
from typing import Any

import jwt

from app.config import get_settings


def _encode(payload: dict[str, Any], ttl_seconds: int) -> str:
    settings = get_settings()
    now = int(time.time())
    claims = {**payload, "iat": now, "exp": now + ttl_seconds, "iss": "autoqa"}
    return jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str, role: str) -> str:
    settings = get_settings()
    return _encode(
        {"sub": user_id, "role": role, "type": "access"},
        settings.jwt_access_minutes * 60,
    )


def create_refresh_token(user_id: str) -> str:
    settings = get_settings()
    return _encode({"sub": user_id, "type": "refresh"}, settings.jwt_refresh_days * 86400)


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"expected {expected_type} token")
    return payload
