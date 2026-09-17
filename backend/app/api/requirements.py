"""Requirements & Traceability endpoints (PLAN V2.1)."""
from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
from app.models import Priority, Project, Requirement, RequirementLink, User
from app.requirements.generate import generate_cases_for_requirement
from app.requirements.ingest import RequirementDraft, dedupe_and_normalize, parse_document
from app.requirements.traceability import build_matrix

router = APIRouter(prefix="/api/projects/{project_id}/requirements", tags=["requirements"])


def _serialize(req: Requirement, coverage_count: int | None = None) -> dict:
    out = {
        "id": str(req.id),
        "external_id": req.external_id,
        "source": req.source,
        "source_ref": req.source_ref,
        "title": req.title,
        "description": req.description,
        "priority": req.priority.value if hasattr(req.priority, "value") else str(req.priority),
        "status": req.status,
        "tags": req.tags or [],
        "created_at": req.created_at.isoformat() if req.created_at else None,
    }
    if coverage_count is not None:
        out["coverage_count"] = coverage_count
    return out


class GenerateIn(BaseModel):
    requirement_ids: list[str] = Field(default_factory=list)
    use_llm: bool = False


class RequirementPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    description: str | None = None
    priority: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|approved|verified|blocked)$")
    tags: list[str] | None = None


def _get_owned_requirement(db: Session, project: Project, req_id: str) -> Requirement:
    req = db.execute(
        sa.select(Requirement).where(
            Requirement.id == req_id, Requirement.project_id == project.id
        )
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return req


class TextImportIn(BaseModel):
    text: str = Field(min_length=1)
    source_name: str = Field(default="pasted", max_length=200)


async def _do_import(project: Project, user: User, db: Session,
                     drafts: list[RequirementDraft], filename: str) -> dict:
    if not drafts:
        raise HTTPException(status_code=422, detail="No requirement content found in input")

    existing_ids = set(
        db.execute(
            sa.select(Requirement.external_id).where(Requirement.project_id == project.id)
        ).scalars()
    )
    kept, skipped = dedupe_and_normalize(drafts, existing_ids=existing_ids)
    if not kept:
        raise HTTPException(
            status_code=409,
            detail=f"All {len(skipped)} requirements already exist (duplicates: {', '.join(skipped[:5])}…)",
        )

    created: list[Requirement] = []
    for d in kept:
        req = Requirement(
            project_id=project.id,
            external_id=d.external_id,
            source=d.source,
            source_ref=d.source_ref,
            title=d.title,
            description=d.description,
            tags=d.tags,
            status="draft",
        )
        db.add(req)
        created.append(req)
    db.commit()
    audit_record(
        "requirements.imported", user.id, "project", str(project.id),
        {"imported": len(created), "skipped": len(skipped), "file": filename},
    )
    return {
        "imported": len(created),
        "skipped": len(skipped),
        "skipped_ids": skipped,
        "items": [_serialize(r) for r in created],
    }


@router.post("/import", status_code=201)
async def import_requirements_text(
    body: TextImportIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Paste requirements as plain text (sections split on blank lines or headings)."""
    try:
        drafts = parse_document(f"{body.source_name}.txt", body.text.encode("utf-8"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _do_import(project, user, db, drafts, body.source_name)


@router.post("/import-file", status_code=201)
async def import_requirements_file(
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
) -> dict:
    """Upload a document: .md .txt .pdf .docx .xlsx .csv .json (Jira export)."""
    filename = file.filename or "upload"
    data = await file.read()
    try:
        drafts = parse_document(filename, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _do_import(project, user, db, drafts, filename)


@router.post("/generate-cases")
def generate_for_requirements(
    body: GenerateIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Generate review-gated test cases for the given requirements."""
    if body.requirement_ids:
        reqs = db.execute(
            sa.select(Requirement).where(
                Requirement.project_id == project.id,
                Requirement.id.in_(body.requirement_ids),
            )
        ).scalars().all()
    else:
        reqs = db.execute(
            sa.select(Requirement).where(Requirement.project_id == project.id)
        ).scalars().all()
    if not reqs:
        raise HTTPException(status_code=404, detail="No requirements found")

    per: dict[str, dict] = {}
    total_created = 0
    total_skipped = 0
    for req in reqs:
        try:
            created, skipped = generate_cases_for_requirement(
                db, project, req, use_llm=body.use_llm
            )
        except Exception as exc:  # one bad requirement must not sink the batch
            per[req.external_id] = {"error": str(exc)[:200]}
            db.rollback()
            continue
        per[req.external_id] = {"created": len(created), "skipped": len(skipped),
                                "refs": [c.ref for c in created]}
        total_created += len(created)
        total_skipped += len(skipped)

    audit_record(
        "requirements.generate_cases", user.id, "project", str(project.id),
        {"created": total_created, "skipped": total_skipped, "use_llm": body.use_llm},
    )
    return {
        "generated": total_created,
        "skipped": total_skipped,
        "per_requirement": per,
    }


@router.get("/traceability")
def traceability(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    return build_matrix(db, project)


@router.get("")
def list_requirements(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> list[dict]:
    reqs = db.execute(
        sa.select(Requirement)
        .where(Requirement.project_id == project.id)
        .order_by(Requirement.external_id)
    ).scalars().all()
    counts: dict[str, int] = {}
    if reqs:
        link_rows = db.execute(
            sa.select(RequirementLink.requirement_id, sa.func.count(sa.distinct(RequirementLink.test_case_id)))
            .where(RequirementLink.project_id == project.id)
            .group_by(RequirementLink.requirement_id)
        ).all()
        counts = {str(rid): n for rid, n in link_rows}
    return [_serialize(r, coverage_count=counts.get(str(r.id), 0)) for r in reqs]


@router.get("/{req_id}")
def get_requirement(
    req_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    req = _get_owned_requirement(db, project, req_id)
    from app.models import TestCase

    cases = db.execute(
        sa.select(TestCase)
        .join(RequirementLink, RequirementLink.test_case_id == TestCase.id)
        .where(RequirementLink.requirement_id == req.id)
        .order_by(TestCase.ref)
    ).scalars().all()
    return {
        **_serialize(req),
        "cases": [
            {"ref": c.ref, "scenario": c.scenario, "kind": c.kind,
             "approved": c.approved, "enabled": c.enabled}
            for c in cases
        ],
    }


@router.patch("/{req_id}")
def patch_requirement(
    req_id: str,
    body: RequirementPatch,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    req = _get_owned_requirement(db, project, req_id)
    if body.title is not None:
        req.title = body.title.strip() or req.title
    if body.description is not None:
        req.description = body.description
    if body.priority is not None:
        try:
            req.priority = Priority(body.priority)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid priority") from exc
    if body.status is not None:
        req.status = body.status
    if body.tags is not None:
        req.tags = body.tags
    db.commit()
    audit_record("requirements.updated", user.id, "requirement", str(req.id), {})
    return _serialize(req)


@router.delete("/{req_id}", status_code=204)
def delete_requirement(
    req_id: str,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    req = _get_owned_requirement(db, project, req_id)
    db.delete(req)  # links cascade; cases are kept
    db.commit()
    audit_record("requirements.deleted", user.id, "requirement", str(req.id), {})
