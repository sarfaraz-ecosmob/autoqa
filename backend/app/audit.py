"""Audit logging (spec §25)."""
import sqlalchemy as sa

from app.db import SessionLocal
from app.models import AuditLog


def record(action: str, user_id: str | None, entity_type: str = "", entity_id: str = "", detail: dict | None = None) -> None:
    """Write an audit entry in its own short-lived session (never blocks the
    request path on failures)."""
    try:
        db = SessionLocal()
        try:
            db.add(
                AuditLog(
                    user_id=user_id,
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    detail=detail or {},
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        pass
