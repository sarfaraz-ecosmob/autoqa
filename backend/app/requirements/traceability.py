"""Traceability matrix assembly (PLAN V2.1).

Requirement → linked cases → latest execution result per case → defect count.
Set-based queries only (no N+1 per requirement).

Note on the "latest per case" query: rows are ordered by case_id ASC and
created_at DESC, so the *first* row seen for each case_id is its most recent
execution.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import (
    Defect,
    Project,
    Requirement,
    RequirementLink,
    TestExecution,
    TestRun,
    TestCase,
)


def latest_results_per_case(db: Session, project_id: str) -> dict[str, str]:
    """case_id → latest TestExecution.status; cases with no executions are absent."""
    rows = db.execute(
        sa.select(TestExecution.test_case_id, TestExecution.status)
        .join(TestRun, TestRun.id == TestExecution.test_run_id)
        .where(TestRun.project_id == project_id)
        .order_by(TestExecution.test_case_id.asc(), TestExecution.created_at.desc())
    ).all()
    latest: dict[str, str] = {}
    for case_id, status in rows:
        if case_id not in latest:
            latest[case_id] = status.value if hasattr(status, "value") else str(status)
    return latest


def build_matrix(db: Session, project: Project) -> dict:
    """Full traceability payload: rows per requirement + coverage summary."""
    requirements = db.execute(
        sa.select(Requirement)
        .where(Requirement.project_id == project.id)
        .order_by(Requirement.external_id)
    ).scalars().all()

    links = db.execute(
        sa.select(RequirementLink, TestCase).join(
            TestCase, TestCase.id == RequirementLink.test_case_id
        ).where(RequirementLink.project_id == project.id)
    ).all()

    cases_by_req: dict[str, list[dict]] = {}
    for link, case in links:
        cases_by_req.setdefault(str(link.requirement_id), []).append({
            "id": str(case.id),
            "ref": case.ref,
            "scenario": case.scenario,
            "kind": case.kind,
            "approved": case.approved,
        })

    latest = latest_results_per_case(db, str(project.id))

    # Defect counts per requirement: link → case → execution → defect
    defect_rows = db.execute(
        sa.select(RequirementLink.requirement_id, sa.func.count(sa.distinct(Defect.id)))
        .join(TestCase, TestCase.id == RequirementLink.test_case_id)
        .join(TestExecution, TestExecution.test_case_id == TestCase.id)
        .join(Defect, Defect.execution_id == TestExecution.id)
        .where(RequirementLink.project_id == project.id)
        .group_by(RequirementLink.requirement_id)
    ).all()
    defects_by_req = {str(req_id): count for req_id, count in defect_rows}

    rows = []
    covered = 0
    pass_count = 0
    result_count = 0
    for req in requirements:
        req_id = str(req.id)
        cases = cases_by_req.get(req_id, [])
        # latest_results_per_case is keyed by case id
        results = [latest.get(c["id"], "never-run") for c in cases]
        rows.append({
            "external_id": req.external_id,
            "title": req.title,
            "priority": req.priority.value if hasattr(req.priority, "value") else str(req.priority),
            "status": req.status,
            "cases": [{k: v for k, v in c.items() if k != "id"} for c in cases],
            "results": results,
            "defect_count": defects_by_req.get(req_id, 0),
        })
        if cases:
            covered += 1
        for r in results:
            if r != "never-run":
                result_count += 1
                if r == "passed":
                    pass_count += 1

    summary = {
        "requirements": len(requirements),
        "covered": covered,
        "uncovered": len(requirements) - covered,
        "pass_rate": round(pass_count / result_count, 4) if result_count else None,
    }
    return {"rows": rows, "summary": summary}
