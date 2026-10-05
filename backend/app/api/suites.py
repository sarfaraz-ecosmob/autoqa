"""Test suite CRUD (saved groups of test cases, spec §18/§24).

A suite is a named, reusable selection of test cases. Generation auto-creates
`smoke`/`regression` suites; users can create their own from selected cases and
re-run the same group later with one click.

A suite may pin a **test plan version** (`test_plan_id`): the group then
executes only cases that belong to that plan version.

A suite may also declare **test types** (`test_types`, e.g. functional,
security, api, integration): the group runs only cases whose category
matches. Empty list = no type restriction.
"""
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
from app.models import Project, TestCase, TestCategory, TestPlan, TestSuite, User

router = APIRouter(prefix="/api/projects/{project_id}/suites", tags=["test-suites"])


def _validate_test_types(test_types: list[str]) -> list[str]:
    """Dedupe + validate group test types against TestCase categories."""
    out: list[str] = []
    for t in test_types:
        try:
            value = TestCategory(t)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown test type: {t} — must be one of {', '.join(c.value for c in TestCategory)}",
            )
        if value not in out:
            out.append(value)
    return out


class SuiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    refs: list[str] = Field(default_factory=list)  # test-case refs in this group
    test_plan_id: str | None = None  # pin the group to a plan version
    test_types: list[str] = Field(default_factory=list)  # e.g. ["functional", "security"]


class SuiteUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    refs: list[str] | None = None
    test_plan_id: str | None = None
    test_types: list[str] | None = None  # [] = clear (run all types)
    unset_test_plan: bool = False  # explicit way to clear the plan pin


def _plan_label(db: Session, project_id: str, plan_id: str | None) -> str | None:
    if not plan_id:
        return None
    plan = db.execute(
        sa.select(TestPlan).where(TestPlan.id == plan_id, TestPlan.project_id == project_id)
    ).scalar_one_or_none()
    return f"v{plan.version}" if plan else None


def _serialize(db: Session, suite: TestSuite) -> dict:
    return {
        "id": suite.id,
        "name": suite.name,
        "test_plan_id": suite.test_plan_id,
        "test_plan_label": _plan_label(db, suite.project_id, suite.test_plan_id),
        "test_types": suite.test_types or [],
        "test_case_ids": suite.test_case_ids or [],
        "case_count": len(suite.test_case_ids or []),
        "created_at": suite.created_at.isoformat(),
        "updated_at": suite.updated_at.isoformat(),
    }


def _validate_plan(db: Session, project_id: str, plan_id: str | None) -> None:
    if plan_id is None:
        return
    plan = db.execute(
        sa.select(TestPlan).where(TestPlan.id == plan_id, TestPlan.project_id == project_id)
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Test plan not found")


def _validate_refs(db: Session, project_id: str, refs: list[str]) -> None:
    if not refs:
        return
    known = set(
        db.execute(
            sa.select(TestCase.ref).where(
                TestCase.project_id == project_id, TestCase.ref.in_(refs)
            )
        ).scalars()
    )
    unknown = sorted(set(refs) - known)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown test case refs: {', '.join(unknown[:5])}")


@router.get("")
def list_suites(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        sa.select(TestSuite).where(TestSuite.project_id == project.id).order_by(TestSuite.name)
    ).scalars()
    return [_serialize(db, s) for s in rows]


@router.get("/plan-versions")
def plan_versions(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Available test plan versions to pin a group to (newest first)."""
    plans = db.execute(
        sa.select(TestPlan)
        .where(TestPlan.project_id == project.id)
        .order_by(TestPlan.version.desc())
    ).scalars()
    return [
        {
            "id": p.id,
            "version": p.version,
            "label": f"v{p.version}",
            "objective": p.objective,
            "approved": p.approved,
            "created_at": p.created_at.isoformat(),
        }
        for p in plans
    ]


@router.post("", status_code=201)
def create_suite(
    body: SuiteIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Suite name is required")
    existing = db.execute(
        sa.select(TestSuite).where(TestSuite.project_id == project.id, TestSuite.name == name)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Suite '{name}' already exists")

    _validate_plan(db, project.id, body.test_plan_id)
    _validate_refs(db, project.id, body.refs)

    suite = TestSuite(
        project_id=project.id,
        name=name,
        test_plan_id=body.test_plan_id,
        test_types=_validate_test_types(body.test_types),
        test_case_ids=list(dict.fromkeys(body.refs)),  # dedupe, preserve order
    )
    db.add(suite)
    db.commit()
    audit_record(
        "suite.create", user.id, "test_suite", suite.id,
        {"cases": len(suite.test_case_ids or []), "test_plan_id": suite.test_plan_id},
    )
    return _serialize(db, suite)


@router.get("/{suite_id}")
def get_suite(
    suite_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    suite = db.execute(
        sa.select(TestSuite).where(TestSuite.id == suite_id, TestSuite.project_id == project.id)
    ).scalar_one_or_none()
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found")
    return _serialize(db, suite)


@router.patch("/{suite_id}")
def update_suite(
    suite_id: str,
    body: SuiteUpdateIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    suite = db.execute(
        sa.select(TestSuite).where(TestSuite.id == suite_id, TestSuite.project_id == project.id)
    ).scalar_one_or_none()
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found")

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Suite name is required")
        clash = db.execute(
            sa.select(TestSuite).where(
                TestSuite.project_id == project.id,
                TestSuite.name == name,
                TestSuite.id != suite.id,
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(status_code=409, detail=f"Suite '{name}' already exists")
        suite.name = name
    if body.unset_test_plan:
        suite.test_plan_id = None
    elif body.test_plan_id is not None:
        _validate_plan(db, project.id, body.test_plan_id)
        suite.test_plan_id = body.test_plan_id
    if body.test_types is not None:
        suite.test_types = _validate_test_types(body.test_types)
    if body.refs is not None:
        _validate_refs(db, project.id, body.refs)
        suite.test_case_ids = list(dict.fromkeys(body.refs))

    db.commit()
    audit_record("suite.update", user.id, "test_suite", suite.id, {"name": suite.name})
    return _serialize(db, suite)


@router.delete("/{suite_id}", status_code=204)
def delete_suite(
    suite_id: str,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    suite = db.execute(
        sa.select(TestSuite).where(TestSuite.id == suite_id, TestSuite.project_id == project.id)
    ).scalar_one_or_none()
    if suite is None:
        raise HTTPException(status_code=404, detail="Suite not found")
    db.execute(sa.delete(TestSuite).where(TestSuite.id == suite.id))
    db.commit()
    audit_record("suite.delete", user.id, "test_suite", suite.id, {"name": suite.name})
