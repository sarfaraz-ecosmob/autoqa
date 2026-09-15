"""Architecture & API discovery endpoints (spec §24)."""
import json

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
from app.discovery.analyze import (
    build_architecture_map,
    detect_frontend,
    parse_openapi,
    summarize_requests,
)
from app.models import ApiEndpoint, Application, Page, Project, User
from app.tasks import get_redis

router = APIRouter(prefix="/api/projects/{project_id}/analyze", tags=["analysis"])


class OpenApiIn(BaseModel):
    spec: dict  # inline JSON spec


def _captured_requests(scan_id: str | None) -> list[dict]:
    """Load requests captured by the most recent scan (stored in Redis)."""
    r = get_redis()
    if r is None:
        return []
    if scan_id:
        raw = r.get(f"scan:{scan_id}:requests")
        if raw:
            return json.loads(raw)
        return []
    # fall back: latest scan-* key (best effort)
    for key in r.scan_iter(match="scan:*:requests", count=100):
        return json.loads(r.get(key))
    return []


@router.post("", status_code=200)
def run_analysis(
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Analyze crawl evidence: frontend stack, API inventory, architecture map."""
    pages = db.execute(sa.select(Page).where(Page.project_id == project.id)).scalars().all()
    if not pages:
        raise HTTPException(status_code=409, detail="Run a discovery scan first (POST /scan)")

    requests = _captured_requests((project.settings or {}).get("last_scan_id"))

    script_urls = [r["url"] for r in requests if r.get("resource_type") == "script"]
    css_urls = [r["url"] for r in requests if r.get("resource_type") == "stylesheet"]
    frontend = detect_frontend(
        [p.components.get("raw_html", "") or "" for p in pages], script_urls, css_urls
    )
    api_rows = summarize_requests(requests, project.base_url)

    # Persist/refresh API endpoints (dedupe on method+path)
    for row in api_rows:
        existing = db.execute(
            sa.select(ApiEndpoint).where(
                ApiEndpoint.project_id == project.id,
                ApiEndpoint.method == row["method"],
                ApiEndpoint.path == row["path"],
            )
        ).scalar_one_or_none()
        values = {
            "group": row["group"],
            "sample_headers": {},
            "auth_required": False,
            "source": "observed",
        }
        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            db.add(ApiEndpoint(project_id=project.id, method=row["method"], path=row["path"], **values))

    arch_map = build_architecture_map(frontend, len(api_rows), project.base_url)

    app_row = db.execute(
        sa.select(Application).where(Application.project_id == project.id)
    ).scalar_one_or_none()
    if app_row is None:
        app_row = Application(project_id=project.id)
        db.add(app_row)
    app_row.frontend_stack = frontend
    app_row.backend_stack = {"observed_api_groups": sorted({r["group"] for r in api_rows})}
    app_row.architecture_map = arch_map

    db.commit()
    audit_record("analysis.run", user.id, "project", project.id)
    return {
        "frontend": frontend,
        "apis": api_rows,
        "architecture": arch_map,
        "pages_analyzed": len(pages),
    }


@router.get("/architecture")
def get_architecture(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    app_row = db.execute(
        sa.select(Application).where(Application.project_id == project.id)
    ).scalar_one_or_none()
    if app_row is None:
        raise HTTPException(status_code=404, detail="No analysis yet — POST /analyze")
    return {
        "frontend": app_row.frontend_stack,
        "backend": app_row.backend_stack,
        "architecture": app_row.architecture_map,
    }


@router.get("/apis")
def list_apis(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        sa.select(ApiEndpoint)
        .where(ApiEndpoint.project_id == project.id)
        .order_by(ApiEndpoint.group, ApiEndpoint.path)
    ).scalars()
    return [
        {
            "id": a.id,
            "method": a.method,
            "path": a.path,
            "group": a.group,
            "source": a.source,
            "auth_required": a.auth_required,
        }
        for a in rows
    ]


@router.post("/openapi", status_code=201)
def import_openapi(
    body: OpenApiIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Import an OpenAPI/Swagger JSON spec (spec §5)."""
    try:
        rows = parse_openapi(body.spec)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid OpenAPI spec: {exc}") from exc

    added = 0
    for row in rows:
        existing = db.execute(
            sa.select(ApiEndpoint).where(
                ApiEndpoint.project_id == project.id,
                ApiEndpoint.method == row["method"],
                ApiEndpoint.path == row["path"],
            )
        ).scalar_one_or_none()
        if existing:
            existing.source = "openapi"
            existing.group = row["group"]
            existing.request_schema = row["request_schema"]
            existing.response_schema = row["response_schema"]
            existing.auth_required = row["auth_required"]
        else:
            db.add(
                ApiEndpoint(
                    project_id=project.id,
                    method=row["method"],
                    path=row["path"],
                    group=row["group"],
                    source="openapi",
                    request_schema=row["request_schema"],
                    response_schema=row["response_schema"],
                    auth_required=row["auth_required"],
                )
            )
            added += 1
    db.commit()
    audit_record("openapi.import", user.id, "project", project.id, {"endpoints": len(rows)})
    return {"imported": len(rows), "new": added}


@router.post("/openapi/upload", status_code=201)
def upload_openapi(
    file: UploadFile = File(...),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    raw = file.file.read()
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="File is not valid JSON") from exc
    rows = parse_openapi(spec)
    for row in rows:
        db.add(ApiEndpoint(project_id=project.id, **{k: v for k, v in row.items() if k in ("method", "path", "group", "request_schema", "response_schema", "auth_required")}, source="openapi"))
    db.commit()
    return {"imported": len(rows)}
