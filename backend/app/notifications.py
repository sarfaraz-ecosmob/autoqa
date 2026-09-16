"""In-app notification service (§31 UX, Phase 14).

Workers call notify_admins() / notify_user() after notable events. Only
events enabled in the user's notification_settings are stored. Email delivery
is a stub (email_enabled captured in settings; SMTP wiring is Phase 15+).
"""
import sqlalchemy as sa

from app.db import SessionLocal
from app.models import Notification, NotificationSetting, Role, User


DEFAULT_EVENTS = {
    "run_completed": True,
    "run_failed_tests": True,
    "security_scan_completed": True,
    "report_ready": True,
    "a11y_critical": False,
    "perf_breach": False,
}


def _settings_for(db, user_id: str) -> NotificationSetting:
    row = db.execute(
        sa.select(NotificationSetting).where(NotificationSetting.user_id == user_id)
    ).scalar_one_or_none()
    if row is None:
        row = NotificationSetting(user_id=user_id, events=dict(DEFAULT_EVENTS))
        db.add(row)
        db.flush()
    if not row.events:
        row.events = dict(DEFAULT_EVENTS)
    return row


def notify_user(db, user_id: str, kind: str, title: str, body: str = "", link: str = "", project_id: str | None = None) -> None:
    row = _settings_for(db, user_id)
    if not (row.events or {}).get(kind, False):
        return  # user disabled this event type
    db.add(
        Notification(
            user_id=user_id,
            project_id=project_id,
            kind=kind,
            title=title[:300],
            body=body[:2000],
            link=link[:500],
        )
    )
    db.commit()


def notify_admins(kind: str, title: str, body: str = "", link: str = "", project_id: str | None = None) -> None:
    """Fan-out to every admin (used by workers; opens its own session)."""
    db = SessionLocal()
    try:
        admins = db.execute(sa.select(User).where(User.role == Role.admin, User.is_active.is_(True))).scalars().all()
        for admin in admins:
            row = _settings_for(db, admin.id)
            if not (row.events or {}).get(kind, False):
                continue
            db.add(
                Notification(
                    user_id=admin.id,
                    project_id=project_id,
                    kind=kind,
                    title=title[:300],
                    body=body[:2000],
                    link=link[:500],
    )
            )
        db.commit()
    finally:
        db.close()


def unread_count(db, user_id: str) -> int:
    return db.execute(
        sa.select(sa.func.count()).select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
    ).scalar_one()
