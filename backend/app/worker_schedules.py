"""Scheduled runs (Tier-1, Katalon TestOps-style) + parallel dispatch.

Celery beat calls `app.dispatch_due_schedules` every minute; each due
TestSchedule creates a TestRun (reusing the same selection/dispatch rules as
manual runs, including test-data resolution and parallelism) and stamps
next_run_at via croniter.
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from croniter import croniter

from app.db import SessionLocal
from app.models import Project, TestSchedule
from app.tasks import celery_app


@celery_app.task(name="app.dispatch_due_schedules")
def dispatch_due_schedules() -> dict:
    """Beat entrypoint (every minute): find schedules whose next_run_at is due."""
    db = SessionLocal()
    triggered: list[str] = []
    try:
        now = datetime.now(timezone.utc)
        due = db.execute(
            sa.select(TestSchedule).where(
                TestSchedule.is_active.is_(True),
                sa.or_(
                    TestSchedule.next_run_at.is_(None),
                    TestSchedule.next_run_at <= now,
                ),
            )
        ).scalars().all()
        for sched in due:
            project = db.execute(
                sa.select(Project).where(Project.id == sched.project_id)
            ).scalar_one_or_none()
            if project is None or not project.authorization_confirmed:
                _advance(sched, db)
                continue
            try:
                run_id = create_scheduled_run(sched)
                sched.last_run_id = run_id
                sched.last_run_at = now
                triggered.append(f"{sched.id}:{run_id}")
            except Exception:
                pass  # a broken schedule must not block the others
            _advance(sched, db)
        db.commit()
        return {"triggered": triggered}
    finally:
        db.close()


def _advance(sched: TestSchedule, db) -> None:
    """Compute the next occurrence from now (never re-triggers in the past)."""
    try:
        base = datetime.now(timezone.utc)
        sched.next_run_at = croniter(sched.cron, base).get_next(datetime)
    except Exception:
        sched.is_active = False  # invalid cron → disable rather than spin


def create_scheduled_run(sched: TestSchedule) -> str:
    """Build and dispatch a run for a schedule; returns run_id."""
    from app.api.testruns import _select_cases, RunIn
    from app.models import RunStatus, TestRun
    from app.services.runs import dispatch_run

    db = SessionLocal()
    try:
        project = db.execute(
            sa.select(Project).where(Project.id == sched.project_id)
        ).scalar_one_or_none()
        if project is None:
            raise RuntimeError("project missing")

        body = RunIn(
            label=f"Scheduled: {sched.name}",
            browsers=sched.browsers or ["chromium"],
            suite=sched.suite,
            refs=sched.refs or None,
            max_retries=sched.max_retries,
            environment_id=sched.environment_id,
        )
        cases = _select_cases(db, project.id, body)
        if not cases:
            raise RuntimeError("no approved+enabled cases match schedule")

        run = TestRun(
            project_id=project.id,
            label=body.label,
            status=RunStatus.pending,
            browsers=[b for b in body.browsers if b in ("chromium", "firefox", "webkit")],
            config={
                "max_retries": body.max_retries,
                "suite": body.suite,
                "refs": body.refs,
                "environment_id": body.environment_id,
                "scheduled": True,
            },
        )
        db.add(run)
        db.flush()

        from app.models import TestExecution, TestStatus
        import uuid as uuid_mod

        browsers = run.browsers or ["chromium"]
        for case in cases:
            for browser in browsers:
                if case.kind == "api" and browser != browsers[0]:
                    continue
                db.add(
                    TestExecution(
                        id=uuid_mod.uuid4().hex,
                        test_run_id=run.id,
                        test_case_id=case.id,
                        browser="api" if case.kind == "api" else browser,
                        status=TestStatus.queued,
                    )
                )
        db.commit()

        dispatch_run(project, run, parallelism=max(1, sched.parallelism))
        return run.id
    finally:
        db.close()
