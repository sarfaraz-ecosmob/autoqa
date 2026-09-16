"""Test run endpoints (spec §18, §24): create/start/pause/resume/stop/progress."""
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
from app.models import (
    Priority,
    Project,
    RunStatus,
    TestCase,
    TestExecution,
    TestRun,
    TestStatus,
    User,
)
from app.tasks import celery_app

router = APIRouter(prefix="/api/projects/{project_id}/test-runs", tags=["test-runs"])


class RunIn(BaseModel):
    label: str = Field(default="", max_length=200)
    browsers: list[str] = Field(default_factory=lambda: ["chromium"])
    suite: str | None = None  # smoke | regression | None=all
    modules: list[str] | None = None
    priorities: list[str] | None = None
    refs: list[str] | None = None  # explicit selected tests
    max_retries: int = Field(default=0, ge=0, le=5)
    viewport: dict | None = None
    environment_id: str | None = None  # §17 test data: environment + datasets to resolve


class RunControl(BaseModel):
    reason: str = ""


def _serialize(run: TestRun) -> dict:
    return {
        "id": run.id,
        "label": run.label,
        "status": run.status.value,
        "browsers": run.browsers,
        "config": run.config,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "created_at": run.created_at.isoformat(),
    }


def _select_cases(db: Session, project_id: str, body: RunIn) -> list[TestCase]:
    stmt = sa.select(TestCase).where(
        TestCase.project_id == project_id,
        TestCase.approved.is_(True),
        TestCase.enabled.is_(True),
    )
    if body.refs:
        stmt = stmt.where(TestCase.ref.in_(body.refs))
    elif body.modules:
        stmt = stmt.where(TestCase.module.in_(body.modules))
    elif body.priorities:
        stmt = stmt.where(TestCase.priority.in_([Priority(p) for p in body.priorities]))
    rows = db.execute(stmt.order_by(TestCase.priority, TestCase.ref)).scalars().all()
    if body.suite:
        from app.models import TestSuite

        suite = db.execute(
            sa.select(TestSuite).where(TestSuite.project_id == project_id, TestSuite.name == body.suite)
        ).scalar_one_or_none()
        allowed = set(suite.test_case_ids or []) if suite else set()
        rows = [r for r in rows if r.ref in allowed]
    return rows


@router.post("", status_code=201)
def create_run(
    body: RunIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not project.authorization_confirmed:
        raise HTTPException(status_code=409, detail="Confirm authorization before executing tests")
    cases = _select_cases(db, project.id, body)
    if not cases:
        raise HTTPException(status_code=409, detail="No approved+enabled test cases match this selection")

    run = TestRun(
        project_id=project.id,
        label=body.label or f"Run {uuid.uuid4().hex[:6]}",
        status=RunStatus.pending,
        browsers=[b for b in body.browsers if b in ("chromium", "firefox", "webkit")],
        config={
            "max_retries": body.max_retries,
            "suite": body.suite,
            "modules": body.modules,
            "priorities": body.priorities,
            "refs": body.refs,
            "viewport": body.viewport,
            "environment_id": body.environment_id,
        },
    )
    db.add(run)
    db.flush()

    browsers = run.browsers or ["chromium"]
    for case in cases:
        for browser in browsers:
            if case.kind == "api" and browser != browsers[0]:
                continue  # API cases run once, not per browser
            db.add(
                TestExecution(
                    id=uuid.uuid4().hex,
                    test_run_id=run.id,
                    test_case_id=case.id,
                    browser="api" if case.kind == "api" else browser,
                    status=TestStatus.queued,
                )
            )
    db.commit()
    audit_record("testrun.create", user.id, "test_run", run.id, {"cases": len(cases)})
    return _serialize(run)


@router.get("")
def list_runs(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        sa.select(TestRun).where(TestRun.project_id == project.id).order_by(TestRun.created_at.desc())
    ).scalars()
    return [_serialize(r) for r in rows]


@router.get("/{run_id}")
def get_run(
    run_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    run = db.execute(
        sa.select(TestRun).where(TestRun.id == run_id, TestRun.project_id == project.id)
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    counts: dict[str, int] = {}
    for status in TestStatus:
        counts[status.value] = db.execute(
            sa.select(sa.func.count()).select_from(TestExecution).where(
                TestExecution.test_run_id == run.id, TestExecution.status == status
            )
        ).scalar_one()
    total = sum(counts.values())
    done = total - counts.get("queued", 0) - counts.get("running", 0)
    return {
        **_serialize(run),
        "progress": {
            "total": total,
            "done": done,
            "percent": int(done * 100 / total) if total else 0,
            **counts,
        },
    }


@router.post("/{run_id}/start", status_code=202)
def start_run(
    run_id: str,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    run = db.execute(
        sa.select(TestRun).where(TestRun.id == run_id, TestRun.project_id == project.id)
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != RunStatus.pending:
        raise HTTPException(status_code=409, detail=f"Run is {run.status.value}, cannot start")

    run.status = RunStatus.running
    run.started_at = sa.func.now()
    db.commit()

    executions = db.execute(
        sa.select(TestExecution).where(
            TestExecution.test_run_id == run.id, TestExecution.status == TestStatus.queued
        )
    ).scalars().all()
    max_retries = int((run.config or {}).get("max_retries", 0))

    # Test data resolution (§17): environment vars + datasets merged into the
    # flat variables dict executors substitute into {{placeholders}}.
    from app.testdata import resolve_variables

    try:
        variables = resolve_variables(project, (run.config or {}).get("environment_id"))
    except Exception:
        variables = {}

    dispatched = 0
    for ex in executions:
        case = db.execute(
            sa.select(TestCase).where(TestCase.id == ex.test_case_id)
        ).scalar_one_or_none()
        if case is None:
            continue
        celery_app.send_task(
            "app.execute_test",
            kwargs={
                "execution_id": ex.id,
                "test_run_id": run.id,
                "project_id": project.id,
                "base_url": project.base_url,
                "case": {
                    "kind": case.kind,
                    "steps": case.steps,
                    "ref": case.ref,
                    "scenario": case.scenario,
                    "capture_on_pass": case.capture_on_pass,
                },
                "variables": variables,
                "browser": ex.browser,
                "attempt": 1,
                "max_attempts": max_retries + 1,
            },
            task_id=f"exec-{ex.id}",
        )
        dispatched += 1

    audit_record("testrun.start", user.id, "test_run", run.id, {"dispatched": dispatched})
    return {"status": "started", "dispatched": dispatched}


@router.post("/{run_id}/pause")
def pause_run(
    run_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    run = db.execute(
        sa.select(TestRun).where(TestRun.id == run_id, TestRun.project_id == project.id)
    ).scalar_one_or_none()
    if run is None or run.status != RunStatus.running:
        raise HTTPException(status_code=409, detail="Run is not running")
    run.status = RunStatus.paused
    db.commit()
    # In-flight tasks finish; queued dispatch is gated on status in start/ resume.
    return {"status": "paused"}


@router.post("/{run_id}/resume")
def resume_run(
    run_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    run = db.execute(
        sa.select(TestRun).where(TestRun.id == run_id, TestRun.project_id == project.id)
    ).scalar_one_or_none()
    if run is None or run.status != RunStatus.paused:
        raise HTTPException(status_code=409, detail="Run is not paused")
    run.status = RunStatus.running
    db.commit()
    queued = db.execute(
        sa.select(sa.func.count()).select_from(TestExecution).where(
            TestExecution.test_run_id == run.id, TestExecution.status == TestStatus.queued
        )
    ).scalar_one()
    return {"status": "running", "queued_remaining": queued}


@router.post("/{run_id}/stop")
def stop_run(
    run_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    run = db.execute(
        sa.select(TestRun).where(TestRun.id == run_id, TestRun.project_id == project.id)
    ).scalar_one_or_none()
    if run is None or run.status not in (RunStatus.running, RunStatus.paused):
        raise HTTPException(status_code=409, detail="Run is not active")
    run.status = RunStatus.stopped
    run.finished_at = sa.func.now()
    db.execute(
        sa.update(TestExecution)
        .where(TestExecution.test_run_id == run.id, TestExecution.status == TestStatus.queued)
        .values(status=TestStatus.skipped)
    )
    db.commit()
    return {"status": "stopped"}


@router.get("/{run_id}/executions")
def list_executions(
    run_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    status: str | None = None,
) -> list[dict]:
    stmt = (
        sa.select(TestExecution, TestCase)
        .join(TestCase, TestCase.id == TestExecution.test_case_id)
        .where(TestExecution.test_run_id == run_id)
    )
    if status:
        stmt = stmt.where(TestExecution.status == TestStatus(status))
    rows = db.execute(stmt).all()
    return [
        {
            "id": ex.id,
            "ref": case.ref,
            "scenario": case.scenario,
            "module": case.module,
            "browser": ex.browser,
            "status": ex.status.value,
            "attempt": ex.attempt,
            "duration_ms": ex.duration_ms,
            "actual_result": ex.actual_result,
            "worker_id": ex.worker_id,
        }
        for ex, case in rows
    ]
