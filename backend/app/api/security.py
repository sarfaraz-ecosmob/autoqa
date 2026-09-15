"""Security endpoints (spec §13): start tiered scans, status, findings."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
from app.models import Project, User
from app.tasks import celery_app

router = APIRouter(prefix="/api/projects/{project_id}/security", tags=["security"])

_TIERS = {"passive", "safe_active", "full"}


class ScanIn(BaseModel):
    tier: str = Field(default="passive", pattern="^(passive|safe_active|full)$")
    page_paths: list[str] = Field(default_factory=list)


class StatusIn(BaseModel):
    scan_id: str


@router.post("/scan", status_code=202)
def start_security_scan(
    body: ScanIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
) -> dict:
    if body.tier not in _TIERS:
        raise HTTPException(status_code=422, detail="tier must be passive|safe_active|full")
    if body.tier == "full" and not project.authorization_confirmed:
        raise HTTPException(
            status_code=403,
            detail="Authorized Full Scan requires explicit authorization confirmation",
        )

    scan_id = uuid.uuid4().hex
    celery_app.send_task(
        "app.run_security_scan",
        kwargs={
            "scan_id": scan_id,
            "progress_channel": f"security:{scan_id}",
            "project_id": project.id,
            "base_url": project.base_url,
            "tier": body.tier,
            "page_paths": body.page_paths,
        },
        task_id=scan_id,
    )
    audit_record(
        "security.scan_start", user.id, "project", project.id, {"tier": body.tier, "scan_id": scan_id}
    )
    return {"scan_id": scan_id, "tier": body.tier, "status": "started"}


@router.get("/scan/status")
def scan_status(
    scan_id: str,
    project: Project = Depends(get_owned_project),
) -> dict:
    from celery.result import AsyncResult

    result = AsyncResult(scan_id, app=celery_app)
    response: dict = {"scan_id": scan_id, "status": result.state}
    if result.ready():
        try:
            response["result"] = result.result
        except Exception:
            response["result"] = None
    return response


@router.get("/findings")
def findings(
    project: Project = Depends(get_owned_project),
    db=Depends(get_db),
    severity: str | None = None,
) -> list[dict]:
    import sqlalchemy as sa

    from app.models import SecurityFinding

    stmt = sa.select(SecurityFinding).where(SecurityFinding.project_id == project.id)
    if severity:
        stmt = stmt.where(SecurityFinding.severity == severity)
    rows = db.execute(stmt.order_by(SecurityFinding.created_at.desc())).scalars()
    return [
        {
            "id": f.id,
            "title": f.title,
            "severity": f.severity.value,
            "description": f.description,
            "evidence": f.evidence,
            "remediation": f.remediation,
            "status": f.status.value,
            "scan_id": f.scan_id,
        }
        for f in rows
    ]
