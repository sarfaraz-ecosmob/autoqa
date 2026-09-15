"""Test case endpoints (spec §24 + §8 review workflow)."""
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
from app.generation.testcases import generate_cases, llm_refine_cases, tag_suites
from app.models import (
    ApiEndpoint,
    Page,
    Priority,
    Project,
    Role,
    TestCase,
    TestSuite,
    User,
)

router = APIRouter(prefix="/api/projects/{project_id}/test-cases", tags=["test-cases"])


class GenerateIn(BaseModel):
    use_llm: bool = False


class ReviewActionIn(BaseModel):
    action: str = Field(pattern="^(approve|reject|disable|enable|duplicate)$")
    priority: str | None = None
    expected_result: str | None = None
    test_data: dict | None = None
    scenario: str | None = None


class ApproveIn(BaseModel):
    refs: list[str] = Field(default_factory=list)  # empty = approve all


def _serialize(tc: TestCase) -> dict:
    return {
        "id": tc.id,
        "ref": tc.ref,
        "scenario": tc.scenario,
        "module": tc.module,
        "category": tc.category.value,
        "priority": tc.priority.value,
        "preconditions": tc.preconditions,
        "test_data": tc.test_data,
        "steps": tc.steps,
        "expected_result": tc.expected_result,
        "kind": tc.kind,
        "enabled": tc.enabled,
        "approved": tc.approved,
    }


@router.post("/generate", status_code=201)
def generate(
    body: GenerateIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    pages = [
        {"url": p.url, "title": p.title, "depth": p.depth, "components": p.components or {}}
        for p in db.execute(sa.select(Page).where(Page.project_id == project.id)).scalars()
    ]
    apis = [
        {"method": a.method, "path": a.path, "group": a.group}
        for a in db.execute(sa.select(ApiEndpoint).where(ApiEndpoint.project_id == project.id)).scalars()
    ]
    if not pages:
        raise HTTPException(status_code=409, detail="Run discovery (POST /scan) first")

    cases = generate_cases(project.base_url, pages, apis, has_credentials=bool(project.credentials_encrypted))
    if body.use_llm:
        cases = llm_refine_cases(cases)
    if not cases:
        raise HTTPException(status_code=409, detail="No test cases could be generated from discovery data")

    # Replace existing cases for a clean regeneration (refs are re-issued)
    db.execute(sa.delete(TestCase).where(TestCase.project_id == project.id))
    db.flush()

    existing_refs: set[str] = set()
    for c in cases:
        ref = c["ref"]
        while ref in existing_refs:
            ref = f"{ref}-D"
        existing_refs.add(ref)
        db.add(
            TestCase(
                project_id=project.id,
                ref=ref,
                scenario=c["scenario"],
                module=c["module"],
                category=c.get("category", "functional"),
                priority=Priority(c.get("priority", "medium")),
                preconditions=c.get("preconditions", ""),
                test_data=c.get("test_data", {}),
                steps=c.get("steps", []),
                expected_result=c.get("expected_result", ""),
                kind=c.get("kind", "browser"),
                approved=False,  # spec §8: nothing auto-executes before review
            )
        )

    suites = tag_suites(cases)
    for name, refs in suites.items():
        suite = TestSuite(project_id=project.id, name=name, test_case_ids=refs)
        db.add(suite)

    db.commit()
    audit_record("testcases.generate", user.id, "project", project.id, {"count": len(cases)})
    return {"generated": len(cases), "suites": {k: len(v) for k, v in suites.items()}}


@router.get("")
def list_cases(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    module: str | None = None,
    priority: str | None = None,
    approved: bool | None = None,
) -> list[dict]:
    stmt = sa.select(TestCase).where(TestCase.project_id == project.id)
    if module:
        stmt = stmt.where(TestCase.module == module)
    if priority:
        stmt = stmt.where(TestCase.priority == Priority(priority))
    if approved is not None:
        stmt = stmt.where(TestCase.approved == approved)
    rows = db.execute(stmt.order_by(TestCase.ref)).scalars()
    return [_serialize(tc) for tc in rows]


@router.patch("/{ref}")
def review_case(
    ref: str,
    body: ReviewActionIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    tc = db.execute(
        sa.select(TestCase).where(TestCase.project_id == project.id, TestCase.ref == ref)
    ).scalar_one_or_none()
    if tc is None:
        raise HTTPException(status_code=404, detail=f"Test case {ref} not found")

    if body.action == "approve":
        tc.approved = True
    elif body.action == "reject":
        tc.approved = False
        tc.enabled = False
    elif body.action == "disable":
        tc.enabled = False
    elif body.action == "enable":
        tc.enabled = True
    elif body.action == "duplicate":
        dup = TestCase(
            project_id=project.id,
            ref=f"{tc.ref}-COPY",
            scenario=tc.scenario,
            module=tc.module,
            category=tc.category,
            priority=tc.priority,
            preconditions=tc.preconditions,
            test_data=tc.test_data,
            steps=tc.steps,
            expected_result=tc.expected_result,
            kind=tc.kind,
        )
        db.add(dup)
        db.commit()
        audit_record("testcases.duplicate", user.id, "test_case", dup.id)
        return _serialize(dup)

    if body.priority:
        tc.priority = Priority(body.priority)
    if body.expected_result is not None:
        tc.expected_result = body.expected_result
    if body.test_data is not None:
        tc.test_data = body.test_data
    if body.scenario is not None:
        tc.scenario = body.scenario

    db.commit()
    audit_record(f"testcases.{body.action}", user.id, "test_case", tc.id)
    return _serialize(tc)


@router.post("/approve")
def approve_cases(
    body: ApproveIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    stmt = sa.select(TestCase).where(TestCase.project_id == project.id)
    if body.refs:
        stmt = stmt.where(TestCase.ref.in_(body.refs))
    rows = db.execute(stmt).scalars().all()
    for tc in rows:
        tc.approved = True
    db.commit()
    audit_record("testcases.approve_batch", user.id, "project", project.id, {"count": len(rows)})
    return {"approved": len(rows)}


@router.delete("/{ref}", status_code=204)
def delete_case(
    ref: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
):
    db.execute(sa.delete(TestCase).where(TestCase.project_id == project.id, TestCase.ref == ref))
    db.commit()
