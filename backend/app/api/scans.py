"""Scan endpoints (spec §24): POST /projects/{id}/scan, GET scan status."""
import json
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
from app.models import Page, Project, User
from app.security.ssrf import validate_target_url
from app.tasks import celery_app

router = APIRouter(prefix="/api/projects/{project_id}/scan", tags=["scans"])


class ScanIn(BaseModel):
    max_depth: int = Field(default=2, ge=1, le=10)
    max_urls: int = Field(default=50, ge=1, le=1000)
    rate_limit_per_sec: float = Field(default=5.0, gt=0, le=50)
    respect_robots: bool = True


@router.post("", status_code=202)
def start_scan(
    body: ScanIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
) -> dict:
    """Dispatch a discovery scan. Requires confirmed authorization (§2 step 4)."""
    if not project.authorization_confirmed:
        raise HTTPException(
            status_code=409,
            detail="Authorization must be confirmed before scanning (POST /authorization {confirmed: true})",
        )
    try:
        validate_target_url(project.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    scan_id = uuid.uuid4().hex
    celery_app.send_task(
        "app.run_scan",
        kwargs={
            "scan_id": scan_id,
            "progress_channel": f"scan:{scan_id}",
            "project_id": project.id,
            "base_url": project.base_url,
            "controls": body.model_dump(),
        },
        task_id=scan_id,  # so AsyncResult(scan_id) resolves
    )
    audit_record("scan.start", user.id, "project", project.id, {"scan_id": scan_id})
    return {"scan_id": scan_id, "status": "started"}


@router.get("/status")
def scan_status(
    scan_id: str,
    project: Project = Depends(get_owned_project),
) -> dict:
    """Fetch the AsyncResult for a scan (simple polling status)."""
    from celery.result import AsyncResult

    result = AsyncResult(scan_id, app=celery_app)
    response: dict = {"scan_id": scan_id, "status": result.state}
    if result.ready():
        try:
            response["result"] = result.result
        except Exception:
            response["result"] = None
    return response


@router.get("/stream")
def scan_stream(
    scan_id: str,
    project: Project = Depends(get_owned_project),
):
    """SSE stream of scan progress (real-time updates without refresh)."""
    from app.tasks import get_redis

    def event_gen():
        r = get_redis()
        if r is None:
            yield "data: {\"error\": \"redis unavailable\"}\n\n"
            return
        pubsub = r.pubsub()
        pubsub.subscribe(f"scan:{scan_id}")
        for message in pubsub.listen():
            if message["type"] == "message":
                yield f"data: {message['data'].decode()}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/pages")
def list_pages(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        sa.select(Page).where(Page.project_id == project.id).order_by(Page.depth, Page.url)
    ).scalars()
    return [
        {
            "id": p.id,
            "url": p.url,
            "title": p.title,
            "depth": p.depth,
            "status_code": p.status_code,
            "components": p.components,
        }
        for p in rows
    ]
