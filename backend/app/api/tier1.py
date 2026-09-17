"""Tier-1 feature endpoints: schedules, webhooks, flaky report, visual testing,
recorder import.

- Schedules: cron-driven recurring runs (croniter-validated)
- Webhooks: outbound signed notifications; URLs encrypted at rest, never
  returned in plaintext after creation
- Flaky: instability analytics over execution history
- Visual: baseline upload/compare/approve (Percy-style) — screenshots stored
  via the shared artifacts storage; checks recorded per execution
- Recorder: Playwright codegen JSON → our step format (human reviews before
  saving as a test case)
"""
import base64
import json

import sqlalchemy as sa
from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import record as audit_record
from app.api.deps import get_current_user, get_owned_project
from app.db import get_db
from app.models import (
    Project,
    Role,
    TestCase,
    TestSchedule,
    User,
    VisualBaseline,
    VisualCheck,
    Webhook,
)
from app.security.crypto import encrypt_secret

router = APIRouter(prefix="/api/projects/{project_id}", tags=["tier-1"])


# ---------------------------------------------------------------- schedules


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    cron: str = Field(min_length=9, max_length=100)  # "0 2 * * *"
    browsers: list[str] = Field(default_factory=lambda: ["chromium"])
    environment_id: str | None = None
    suite: str | None = None
    refs: list[str] = Field(default_factory=list)
    max_retries: int = Field(default=0, ge=0, le=5)
    parallelism: int = Field(default=2, ge=0, le=16)
    is_active: bool = True


def _sched_out(s: TestSchedule) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "cron": s.cron,
        "browsers": s.browsers or [],
        "environment_id": str(s.environment_id) if s.environment_id else None,
        "suite": s.suite,
        "refs": s.refs or [],
        "max_retries": s.max_retries,
        "parallelism": s.parallelism,
        "is_active": s.is_active,
        "last_run_id": s.last_run_id,
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
        "created_at": s.created_at.isoformat(),
    }


@router.get("/schedules")
def list_schedules(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.execute(
        sa.select(TestSchedule)
        .where(TestSchedule.project_id == project.id)
        .order_by(TestSchedule.created_at)
    ).scalars().all()
    return {"items": [_sched_out(s) for s in rows]}


@router.post("/schedules", status_code=201)
def create_schedule(
    body: ScheduleIn,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not _valid_cron(body.cron):
        raise HTTPException(status_code=422, detail="Invalid cron expression (5 fields)")
    row = TestSchedule(
        project_id=project.id,
        name=body.name.strip(),
        cron=body.cron.strip(),
        browsers=[b for b in body.browsers if b in ("chromium", "firefox", "webkit")],
        environment_id=body.environment_id,
        suite=body.suite,
        refs=body.refs,
        max_retries=body.max_retries,
        parallelism=body.parallelism,
        created_by=str(user.id),
        is_active=body.is_active,
    )
    try:
        row.next_run_at = croniter(row.cron, _utcnow()).get_next(_datetime())
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid cron expression")
    db.add(row)
    db.commit()
    audit_record("schedule.created", user.id, "test_schedule", str(row.id), {"project": str(project.id), "cron": row.cron})
    return _sched_out(row)


@router.patch("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: str,
    body: ScheduleIn,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.execute(
        sa.select(TestSchedule).where(
            TestSchedule.id == schedule_id, TestSchedule.project_id == project.id
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if not _valid_cron(body.cron):
        raise HTTPException(status_code=422, detail="Invalid cron expression (5 fields)")
    row.name = body.name.strip()
    row.cron = body.cron.strip()
    row.browsers = [b for b in body.browsers if b in ("chromium", "firefox", "webkit")]
    row.environment_id = body.environment_id
    row.suite = body.suite
    row.refs = body.refs
    row.max_retries = body.max_retries
    row.parallelism = body.parallelism
    row.is_active = body.is_active
    row.next_run_at = croniter(row.cron, _utcnow()).get_next(_datetime())
    db.commit()
    return _sched_out(row)


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(
    schedule_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.execute(
        sa.select(TestSchedule).where(
            TestSchedule.id == schedule_id, TestSchedule.project_id == project.id
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    db.delete(row)
    db.commit()
    return None


@router.post("/schedules/{schedule_id}/trigger")
def trigger_schedule(
    schedule_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Run a schedule right now, regardless of cron."""
    row = db.execute(
        sa.select(TestSchedule).where(
            TestSchedule.id == schedule_id, TestSchedule.project_id == project.id
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    from app.worker_schedules import create_scheduled_run

    try:
        run_id = create_scheduled_run(row)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    row.last_run_id = run_id
    row.last_run_at = _utcnow()
    db.commit()
    audit_record("schedule.triggered", user.id, "test_schedule", str(row.id), {"run": run_id})
    return {"run_id": run_id}


# ---------------------------------------------------------------- webhooks


class WebhookIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=8, max_length=1000)
    events: list[str] = Field(default_factory=list)  # empty = all events
    is_active: bool = True


def _webhook_out(w: Webhook) -> dict:
    return {
        "id": str(w.id),
        "name": w.name,
        "masked_url": _mask_url(w.url_encrypted),
        "events": w.events or [],
        "is_active": w.is_active,
        "last_status": w.last_status,
        "last_delivery_at": w.last_delivery_at.isoformat() if w.last_delivery_at else None,
        "created_at": w.created_at.isoformat(),
    }


def _mask_url(url_encrypted: str) -> str:
    from app.security.crypto import decrypt_secret

    try:
        url = decrypt_secret(url_encrypted) if url_encrypted else ""
    except Exception:
        url = ""
    if not url:
        return ""
    # https://hooks.example.com/S/a/b/c → https://hooks.example.com/••••
    parts = url.split("/")
    return "/".join(parts[:3]) + "/••••"


@router.get("/webhooks")
def list_webhooks(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.execute(
        sa.select(Webhook).where(Webhook.project_id == project.id).order_by(Webhook.created_at)
    ).scalars().all()
    return {"items": [_webhook_out(w) for w in rows]}


@router.post("/webhooks", status_code=201)
def create_webhook(
    body: WebhookIn,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not body.url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="Webhook URL must be http(s)")
    import secrets as pysecrets

    row = Webhook(
        project_id=project.id,
        name=body.name.strip(),
        url_encrypted=encrypt_secret(body.url.strip()),
        events=body.events,
        secret=pysecrets.token_hex(16),
        is_active=body.is_active,
    )
    db.add(row)
    db.commit()
    audit_record("webhook.created", user.id, "webhook", str(row.id), {"project": str(project.id)})
    out = _webhook_out(row)
    out["secret"] = row.secret  # shown once, for receiver-side verification
    return out


@router.patch("/webhooks/{webhook_id}")
def update_webhook(
    webhook_id: str,
    body: WebhookIn,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.execute(
        sa.select(Webhook).where(Webhook.id == webhook_id, Webhook.project_id == project.id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    row.name = body.name.strip()
    if body.url.strip() and not body.url.strip().endswith("••••"):
        if not body.url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="Webhook URL must be http(s)")
        row.url_encrypted = encrypt_secret(body.url.strip())
    row.events = body.events
    row.is_active = body.is_active
    db.commit()
    return _webhook_out(row)


@router.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(
    webhook_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.execute(
        sa.select(Webhook).where(Webhook.id == webhook_id, Webhook.project_id == project.id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    db.delete(row)
    db.commit()
    return None


@router.post("/webhooks/{webhook_id}/test")
def test_webhook(
    webhook_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.execute(
        sa.select(Webhook).where(Webhook.id == webhook_id, Webhook.project_id == project.id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    from app.webhooks import deliver

    result = deliver(
        str(row.id),
        {
            "kind": "webhook.test",
            "title": "AutoQA webhook test",
            "body": f"Test delivery from project '{project.name}' — if you see this, the webhook works.",
            "link": "",
        },
    )
    return result


# ---------------------------------------------------------------- flaky


@router.get("/flaky")
def flaky_report(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.analysis.flaky import analyze_project

    return analyze_project(db, str(project.id))


# ---------------------------------------------------------------- visual


class BaselineUpload(BaseModel):
    test_case_id: str
    browser: str = "chromium"
    image_b64: str = Field(min_length=16)


@router.get("/visual/checks")
def list_visual_checks(
    limit: int = 50,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.execute(
        sa.select(VisualCheck)
        .where(VisualCheck.project_id == project.id)
        .order_by(VisualCheck.created_at.desc())
        .limit(max(1, min(limit, 200)))
    ).scalars().all()
    return {
        "items": [
            {
                "id": str(c.id),
                "test_case_id": str(c.test_case_id),
                "execution_id": str(c.execution_id),
                "browser": c.browser,
                "status": c.status,
                "diff_percent": c.diff_percent,
                "baseline_key": c.baseline_key,
                "current_key": c.current_key,
                "diff_key": c.diff_key,
                "created_at": c.created_at.isoformat(),
            }
            for c in rows
        ]
    }


@router.post("/visual/baseline")
def upload_baseline(
    body: BaselineUpload,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Set/replace the approved baseline for a case × browser (from a UI
    upload or an approved check). Image is PNG bytes, base64-encoded."""
    case = db.execute(
        sa.select(TestCase).where(
            TestCase.id == body.test_case_id, TestCase.project_id == project.id
        )
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    try:
        from app.visual import store_baseline

        out = store_baseline(db, project, case, body.browser, body.image_b64)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    audit_record("visual.baseline_set", user.id, "test_case", str(case.id), {"browser": body.browser})
    return out


@router.post("/visual/checks/{check_id}/approve")
def approve_check(
    check_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Approve a changed screenshot as the new baseline (Percy workflow)."""
    check = db.execute(
        sa.select(VisualCheck).where(
            VisualCheck.id == check_id, VisualCheck.project_id == project.id
        )
    ).scalar_one_or_none()
    if check is None:
        raise HTTPException(status_code=404, detail="Check not found")
    from app.visual import approve_check_baseline

    out = approve_check_baseline(db, project, check)
    audit_record("visual.baseline_approved", user.id, "test_case", str(check.test_case_id), {"check": check_id})
    return out


# ---------------------------------------------------------------- recorder


class RecorderImport(BaseModel):
    """Playwright codegen output (JSON array of actions) or our native steps."""

    name: str = Field(min_length=1, max_length=200)
    module: str = Field(default="imported")
    actions: list[dict] = Field(min_length=1)
    browser: str = "chromium"
    approve: bool = False


@router.post("/recorder/import", status_code=201)
def recorder_import(
    body: RecorderImport,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.recorder import actions_to_steps, steps_summary

    steps, warnings = actions_to_steps(body.actions)
    if not steps:
        raise HTTPException(status_code=422, detail="No translatable actions found in recording")
    module = (body.module or "imported").strip() or "imported"
    existing = db.execute(
        sa.select(sa.func.count()).select_from(TestCase).where(TestCase.project_id == project.id)
    ).scalar_one()
    ref = f"TC-REC-{int(existing) + 1:03d}"
    case = TestCase(
        project_id=project.id,
        ref=ref,
        scenario=f"Recorded flow: {body.name}",
        module=module,
        steps=steps,
        expected_result=steps_summary(steps),
        kind="browser",
        approved=bool(body.approve),
    )
    db.add(case)
    db.commit()
    audit_record("recorder.imported", user.id, "test_case", str(case.id), {"steps": len(steps)})
    return {
        "id": str(case.id),
        "ref": ref,
        "steps": steps,
        "warnings": warnings,
        "approved": case.approved,
    }


# ---------------------------------------------------------------- helpers


def _valid_cron(expr: str) -> bool:
    try:
        croniter(expr)
        return True
    except Exception:
        return False


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _datetime():
    from datetime import datetime

    return datetime
