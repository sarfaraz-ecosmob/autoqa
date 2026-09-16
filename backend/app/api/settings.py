"""Account & platform settings API (§22, §31) — Phase 14.

- Password change (current-password verified, never logged)
- AI provider key management: stored encrypted at rest (Fernet §17), never
  returned in plaintext (masked only); DB config overrides env config
- Notification center: list/read/unread-count + per-event settings
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.audit import record as audit_record
from app.api.deps import get_current_user
from app.config import get_settings
from app.db import get_db
from app.models import (
    LlmSetting,
    Notification,
    NotificationSetting,
    Role,
    User,
)
from app.notifications import DEFAULT_EVENTS, _settings_for, unread_count
from app.security.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.security.passwords import hash_password, verify_password
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ---------------------------------------------------------------- password


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/account/password")
def change_password(
    payload: PasswordChange,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.get(User, user.id)
    if row is None or not verify_password(payload.current_password, row.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="New password must be different")
    row.password_hash = hash_password(payload.new_password)
    db.commit()
    audit_record("user.password_changed", user_id=str(user.id), entity_type="user", entity_id=str(user.id))
    return {"ok": True}


# ------------------------------------------------------------- ai provider


class AIProviderUpdate(BaseModel):
    provider: str = Field(pattern="^(openrouter|openai|none)$")
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=400)


def _llm_row(db: Session) -> LlmSetting:
    row = db.get(LlmSetting, 1)
    if row is None:
        row = LlmSetting(id=1)
        db.add(row)
        db.flush()
    return row


@router.get("/ai")
def get_ai_settings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = get_settings()
    row = _llm_row(db)
    db.commit()  # persist singleton if just created

    configured = bool((row.api_key_encrypted or "").strip())
    env_key = bool((s.llm_api_key or "").strip())
    provider = row.provider if configured or row.provider != "none" else s.llm_provider
    masked = None
    if configured:
        try:
            masked = mask_secret(decrypt_secret(row.api_key_encrypted))
        except Exception:
            masked = None

    status = {
        "provider": provider,
        "model": row.model or "auto",
        "base_url": row.base_url or "",
        "has_key": configured or env_key,
        "masked_key": masked,
        "source": "database" if configured else ("env" if env_key else "none"),
        "env_provider": s.llm_provider,
        "env_has_key": env_key,
    }
    if provider == "openrouter" and status["has_key"]:
        try:
            from app.ai import detect_best_free_model

            status["detected_free_model"] = detect_best_free_model()
        except Exception:
            status["detected_free_model"] = None
    return status


@router.put("/ai")
def update_ai_settings(
    payload: AIProviderUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role != Role.admin:
        raise HTTPException(status_code=403, detail="Admin only")
    row = _llm_row(db)
    row.provider = payload.provider
    if payload.model is not None:
        row.model = payload.model.strip() or "auto"
    if payload.base_url is not None:
        row.base_url = payload.base_url.strip()
    if payload.api_key is not None:  # empty string clears the stored key
        key = payload.api_key.strip()
        row.api_key_encrypted = encrypt_secret(key) if key else ""
    row.updated_by = str(user.id)
    db.commit()
    audit_record(
        "settings.ai_updated",
        user_id=str(user.id),
        entity_type="llm_setting",
        entity_id="1",
        detail={"provider": row.provider, "model": row.model, "key_set": bool(row.api_key_encrypted)},
    )
    return {"ok": True, "provider": row.provider, "model": row.model, "key_set": bool(row.api_key_encrypted)}


@router.post("/ai/test")
def test_ai_connection(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Verify the effective provider answers; reports the resolved model."""
    if user.role != Role.admin:
        raise HTTPException(status_code=403, detail="Admin only")
    db.commit()  # ensure singleton flushed before workers/API read it
    from app.ai import get_llm

    llm = get_llm()
    try:
        reply = llm.complete("Reply with the single word: ok", max_tokens=8)
        return {"ok": bool(reply and reply.strip()), "model": getattr(llm, "model_name", None), "sample": (reply or "")[:120]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}


# ------------------------------------------------------------ notifications


@router.get("/notifications/settings")
def get_notification_settings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _settings_for(db, user.id)
    db.commit()
    return {"events": row.events or dict(DEFAULT_EVENTS), "email_enabled": row.email_enabled, "email_address": row.email_address}


class NotificationSettingsUpdate(BaseModel):
    events: dict[str, bool] | None = None
    email_enabled: bool | None = None
    email_address: str | None = Field(default=None, max_length=320)


@router.put("/notifications/settings")
def update_notification_settings(
    payload: NotificationSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _settings_for(db, user.id)
    if payload.events is not None:
        merged = dict(DEFAULT_EVENTS)
        for key, val in payload.events.items():
            if key in merged:
                merged[key] = bool(val)
        row.events = merged
    if payload.email_enabled is not None:
        row.email_enabled = payload.email_enabled
    if payload.email_address is not None:
        row.email_address = payload.email_address.strip()
    db.commit()
    return {"events": row.events, "email_enabled": row.email_enabled, "email_address": row.email_address}


@router.get("/notifications")
def list_notifications(
    limit: int = 20,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    limit = max(1, min(limit, 100))
    rows = db.execute(
        sa.select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return {
        "unread": unread_count(db, user.id),
        "items": [
            {
                "id": str(n.id),
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "link": n.link,
                "project_id": str(n.project_id) if n.project_id else None,
                "read": n.read_at is not None,
                "created_at": n.created_at.isoformat(),
            }
            for n in rows
        ],
    }


@router.post("/notifications/{notification_id}/read")
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.get(Notification, notification_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Notification not found")
    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    db.execute(
        sa.update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(timezone.utc))
    )
    db.commit()
    return {"ok": True}
