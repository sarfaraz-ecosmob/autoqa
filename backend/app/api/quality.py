"""Quality module endpoints (spec §14, §15, §16) — Phase 11.

- Accessibility: start audit, status, results
- Performance: start bounded smoke run, status, results
- Cross-browser matrix: Test Case × browser → PASS/FAIL/… (§16)
"""
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
from app.models import (
    AccessibilityResult,
    PerformanceResult,
    Project,
    TestCase,
    TestExecution,
    TestRun,
    User,
)
from app.security.ssrf import validate_target_url
from app.tasks import celery_app

router = APIRouter(prefix="/api/projects/{project_id}/quality", tags=["quality"])


# ---------------- Accessibility (§15) ----------------

class A11yIn(BaseModel):
    page_paths: list[str] = Field(default_factory=list)


@router.post("/a11y/audit", status_code=202)
def start_a11y_audit(
    body: A11yIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
) -> dict:
    if not project.authorization_confirmed:
        raise HTTPException(status_code=409, detail="Confirm authorization before auditing")
    try:
        validate_target_url(project.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    audit_id = uuid.uuid4().hex
    celery_app.send_task(
        "app.run_a11y_audit",
        kwargs={
            "audit_id": audit_id,
            "progress_channel": f"a11y:{audit_id}",
            "project_id": project.id,
            "base_url": project.base_url,
            "page_paths": body.page_paths,
        },
        task_id=audit_id,
    )
    audit_record("a11y.audit_start", user.id, "project", project.id, {"audit_id": audit_id})
    return {"audit_id": audit_id, "status": "started"}


@router.get("/a11y/status")
def a11y_status(
    audit_id: str,
    project: Project = Depends(get_owned_project),
) -> dict:
    from celery.result import AsyncResult

    result = AsyncResult(audit_id, app=celery_app)
    response: dict = {"audit_id": audit_id, "status": result.state}
    if result.ready():
        try:
            response["result"] = result.result
        except Exception:
            response["result"] = None
    return response


@router.get("/a11y/results")
def a11y_results(
    project: Project = Depends(get_owned_project),
    db=Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        sa.select(AccessibilityResult)
        .where(AccessibilityResult.project_id == project.id)
        .order_by(AccessibilityResult.page_url)
    ).scalars()
    out = []
    for r in rows:
        by_sev: dict[str, int] = {}
        for v in r.violations or []:
            sev = (v.get("severity") or "minor") if isinstance(v, dict) else "minor"
            by_sev[sev] = by_sev.get(sev, 0) + 1
        out.append(
            {
                "id": r.id,
                "page_url": r.page_url,
                "violations": r.violations,
                "violation_count": len(r.violations or []),
                "by_severity": by_sev,
                "wcag_level": r.wcag_level,
            }
        )
    return out


# ---------------- Performance (§14) ----------------

class PerfIn(BaseModel):
    path: str = Field(default="/", max_length=500)
    concurrent_users: int = Field(default=2, ge=1, le=20)
    duration_seconds: int = Field(default=10, ge=1, le=60)
    requests_per_second: float = Field(default=5.0, gt=0, le=50)
    max_response_time_ms: int = Field(default=3000, ge=100)
    max_requests: int = Field(default=200, ge=1, le=1000)


@router.post("/perf/run", status_code=202)
def start_perf_run(
    body: PerfIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
) -> dict:
    if not project.authorization_confirmed:
        raise HTTPException(status_code=409, detail="Confirm authorization before load testing")
    try:
        validate_target_url(project.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    perf_id = uuid.uuid4().hex
    celery_app.send_task(
        "app.run_perf_test",
        kwargs={
            "perf_id": perf_id,
            "progress_channel": f"perf:{perf_id}",
            "project_id": project.id,
            "base_url": project.base_url,
            "config": body.model_dump(),
        },
        task_id=perf_id,
    )
    audit_record("perf.run_start", user.id, "project", project.id, {"perf_id": perf_id, "path": body.path})
    return {"perf_id": perf_id, "status": "started"}


@router.get("/perf/status")
def perf_status(
    perf_id: str,
    project: Project = Depends(get_owned_project),
) -> dict:
    from celery.result import AsyncResult

    result = AsyncResult(perf_id, app=celery_app)
    response: dict = {"perf_id": perf_id, "status": result.state}
    if result.ready():
        try:
            response["result"] = result.result
        except Exception:
            response["result"] = None
    return response


@router.get("/perf/results")
def perf_results(
    project: Project = Depends(get_owned_project),
    db=Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        sa.select(PerformanceResult)
        .where(PerformanceResult.project_id == project.id)
        .order_by(PerformanceResult.created_at.desc())
        .limit(50)
    ).scalars()
    return [
        {
            "id": r.id,
            "scenario": r.scenario,
            "concurrent_users": r.concurrent_users,
            "duration_seconds": r.duration_seconds,
            "p50_ms": r.p50_ms,
            "p90_ms": r.p90_ms,
            "p95_ms": r.p95_ms,
            "p99_ms": r.p99_ms,
            "throughput_rps": r.throughput_rps,
            "error_rate": r.error_rate,
            "meta": r.meta,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# ---------------- Cross-browser matrix (§16) ----------------

_BROWSERS = ("chromium", "firefox", "webkit")


@router.get("/browser-matrix")
def browser_matrix(
    project: Project = Depends(get_owned_project),
    db=Depends(get_db),
    run_id: str | None = None,
) -> dict:
    """Test Case × browser → status. Latest attempt per (case, browser).

    Scoped to a run when run_id is given; otherwise across the project's
    most recent executions per (case, browser).
    """
    stmt = (
        sa.select(TestExecution, TestCase)
        .join(TestCase, TestCase.id == TestExecution.test_case_id)
        .where(
            TestCase.project_id == project.id,
            TestExecution.browser.in_(_BROWSERS),
        )
    )
    if run_id:
        run = db.execute(
            sa.select(TestRun).where(TestRun.id == run_id, TestRun.project_id == project.id)
        ).scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        stmt = stmt.where(TestExecution.test_run_id == run_id)

    rows = db.execute(stmt.order_by(TestCase.ref, TestExecution.attempt)).all()

    # Latest attempt wins per (case, browser)
    latest: dict[tuple[str, str], str] = {}
    cases: dict[str, dict] = {}
    for ex, case in rows:
        latest[(case.ref, ex.browser)] = ex.status.value
        cases[case.ref] = {
            "ref": case.ref,
            "scenario": case.scenario,
            "module": case.module,
            "priority": case.priority.value,
        }

    matrix = []
    for ref in sorted(cases):
        row = {"ref": ref, **cases[ref]}
        for browser in _BROWSERS:
            row[browser] = latest.get((ref, browser), "skipped")
        matrix.append(row)

    return {
        "browsers": list(_BROWSERS),
        "rows": matrix,
        "summary": {
            b: {
                "passed": sum(1 for r in matrix if r[b] == "passed"),
                "failed": sum(1 for r in matrix if r[b] == "failed"),
            }
            for b in _BROWSERS
        },
    }
