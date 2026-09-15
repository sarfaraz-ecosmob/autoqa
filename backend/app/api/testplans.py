"""Test plan endpoints (spec §24): POST/GET /projects/{id}/test-plan."""
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
from app.models import (
    ApiEndpoint,
    Application,
    Page,
    Project,
    TestPlan,
    User,
)
from app.planning.generator import ALL_SECTIONS, build_test_plan

router = APIRouter(prefix="/api/projects/{project_id}/test-plan", tags=["test-plan"])


class PlanApproval(BaseModel):
    approved: bool


def _serialize(plan: TestPlan) -> dict:
    return {
        "id": plan.id,
        "version": plan.version,
        "objective": plan.objective,
        "sections": plan.sections,
        "generated_by": plan.generated_by,
        "approved": plan.approved,
        "created_at": plan.created_at.isoformat(),
    }


def _discovery_context(db: Session, project: Project) -> tuple[list[dict], list[dict], dict]:
    pages = [
        {"url": p.url, "title": p.title, "components": p.components or {}}
        for p in db.execute(sa.select(Page).where(Page.project_id == project.id)).scalars()
    ]
    apis = [
        {"method": a.method, "path": a.path, "group": a.group}
        for a in db.execute(sa.select(ApiEndpoint).where(ApiEndpoint.project_id == project.id)).scalars()
    ]
    app_row = db.execute(
        sa.select(Application).where(Application.project_id == project.id)
    ).scalar_one_or_none()
    frontend = app_row.frontend_stack if app_row else {"frameworks": [], "css": [], "evidence": {}}
    return pages, apis, frontend


@router.post("", status_code=201)
def generate_plan(
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Generate (a new version of) the test plan from discovery evidence."""
    pages, apis, frontend = _discovery_context(db, project)
    if not pages and not apis:
        raise HTTPException(
            status_code=409, detail="Run discovery (POST /scan) before generating a test plan"
        )

    plan_data = build_test_plan(project.name, project.base_url, pages, apis, frontend)

    latest = db.execute(
        sa.select(TestPlan)
        .where(TestPlan.project_id == project.id)
        .order_by(TestPlan.version.desc())
    ).scalars().first()
    version = (latest.version + 1) if latest else 1

    plan = TestPlan(
        project_id=project.id,
        version=version,
        objective=plan_data.get("objective", ""),
        sections=plan_data.get("sections", []),
        generated_by="llm" if _used_llm(plan_data, plan_data.get("sections")) else "heuristic",
        approved=False,
    )
    db.add(plan)
    db.commit()
    audit_record("testplan.generate", user.id, "project", project.id, {"version": version})
    return _serialize(plan)


def _used_llm(plan_data: dict, sections) -> bool:
    """The heuristic builder always emits >= 18 known sections; LLM output
    is accepted as-is, so treat short/unknown-shape plans as LLM-origin."""
    return not (isinstance(sections, list) and len(sections) >= 18)


@router.get("")
def get_plan(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    plan = db.execute(
        sa.select(TestPlan)
        .where(TestPlan.project_id == project.id)
        .order_by(TestPlan.version.desc())
    ).scalars().first()
    if plan is None:
        raise HTTPException(status_code=404, detail="No test plan yet — POST to generate")
    return _serialize(plan)


@router.get("/sections/catalog")
def sections_catalog() -> dict:
    return {"required_sections": ALL_SECTIONS}


@router.patch("/approval")
def approve_plan(
    body: PlanApproval,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    plan = db.execute(
        sa.select(TestPlan)
        .where(TestPlan.project_id == project.id)
        .order_by(TestPlan.version.desc())
    ).scalars().first()
    if plan is None:
        raise HTTPException(status_code=404, detail="No test plan to approve")
    plan.approved = body.approved
    db.commit()
    audit_record("testplan.approval", user.id, "test_plan", plan.id, {"approved": body.approved})
    return _serialize(plan)
