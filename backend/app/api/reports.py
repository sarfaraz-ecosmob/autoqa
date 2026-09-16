"""Report endpoints (spec §19, §24): generate, list, status, download."""
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
from app.models import Project, Report, TestRun, User
from app.tasks import celery_app

router = APIRouter(prefix="/api/projects/{project_id}/reports", tags=["reports"])

_FORMATS = ("csv", "xlsx", "pdf", "html", "json")

_MEDIA_TYPES = {
    "csv": "text/csv",
    "json": "application/json",
    "html": "text/html",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


class ReportIn(BaseModel):
    format: str = Field(pattern="^(csv|xlsx|pdf|html|json)$")
    test_run_id: str | None = None  # None = whole project


def _serialize(r: Report) -> dict:
    return {
        "id": r.id,
        "format": r.format,
        "status": r.status,
        "test_run_id": r.test_run_id,
        "storage_key": r.storage_key,
        "meta": r.meta or {},
        "created_at": r.created_at.isoformat(),
    }


@router.post("", status_code=202)
def create_report(
    body: ReportIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db=Depends(get_db),
) -> dict:
    if body.format not in _FORMATS:
        raise HTTPException(status_code=422, detail=f"format must be one of {', '.join(_FORMATS)}")

    if body.test_run_id:
        run = db.execute(
            sa.select(TestRun).where(
                TestRun.id == body.test_run_id, TestRun.project_id == project.id
            )
        ).scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

    report = Report(
        project_id=project.id,
        test_run_id=body.test_run_id,
        format=body.format,
        status="pending",
    )
    db.add(report)
    db.commit()

    celery_app.send_task(
        "app.generate_report",
        kwargs={
            "report_id": report.id,
            "project_id": project.id,
            "fmt": body.format,
            "test_run_id": body.test_run_id,
        },
        task_id=report.id,
    )
    audit_record(
        "report.create", user.id, "project", project.id,
        {"report_id": report.id, "format": body.format, "run": body.test_run_id},
    )
    return {"report_id": report.id, "format": body.format, "status": "pending"}


@router.get("")
def list_reports(
    project: Project = Depends(get_owned_project),
    db=Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        sa.select(Report)
        .where(Report.project_id == project.id)
        .order_by(Report.created_at.desc())
        .limit(50)
    ).scalars()
    return [_serialize(r) for r in rows]


@router.get("/{report_id}")
def get_report(
    report_id: str,
    project: Project = Depends(get_owned_project),
    db=Depends(get_db),
) -> dict:
    row = db.execute(
        sa.select(Report).where(Report.id == report_id, Report.project_id == project.id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    out = _serialize(row)
    if row.status == "completed":
        from celery.result import AsyncResult

        # Worker writes status to DB; AsyncResult only mirrors the queue state.
        out["queue_state"] = AsyncResult(report_id, app=celery_app).state
    return out


@router.get("/{report_id}/download")
def download_report(
    report_id: str,
    project: Project = Depends(get_owned_project),
    db=Depends(get_db),
) -> Response:
    row = db.execute(
        sa.select(Report).where(Report.id == report_id, Report.project_id == project.id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if row.status != "completed" or not row.storage_key:
        raise HTTPException(status_code=409, detail=f"Report is {row.status}; not downloadable yet")

    from app import storage

    try:
        content = storage.get_bytes(row.storage_key)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Report artifact unavailable: {exc}") from exc

    scope = "run" if row.test_run_id else "project"
    filename = f"autoqa-{scope}-report-{report_id[:8]}.{row.format}"
    return Response(
        content=content,
        media_type=_MEDIA_TYPES.get(row.format, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
