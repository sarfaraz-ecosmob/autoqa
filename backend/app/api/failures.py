"""Failure analysis & defects endpoints (spec §12, §24)."""
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.analysis.failures import analyze_failure, analyze_run
from app.db import get_db
from app.models import Defect, Project, Severity, TestExecution, TestCase, User

router = APIRouter(prefix="/api/projects/{project_id}", tags=["failures"])


class AnalyzeIn(BaseModel):
    run_id: str


@router.post("/analyze-failures")
def run_failure_analysis(
    body: AnalyzeIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
) -> dict:
    """AI-assisted triage of every failed execution in a run (§12)."""
    results = analyze_run(body.run_id)
    audit_record("failures.analyzed", user.id, "test_run", body.run_id, {"defects": len(results)})
    return {"analyzed": len(results), "defects": results}


@router.get("/defects")
def list_defects(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    severity: str | None = None,
) -> list[dict]:
    stmt = sa.select(Defect, TestExecution, TestCase).join(
        TestExecution, TestExecution.id == Defect.execution_id
    ).join(TestCase, TestCase.id == TestExecution.test_case_id).where(
        Defect.project_id == project.id
    )
    if severity:
        stmt = stmt.where(Defect.severity == Severity(severity))
    rows = db.execute(stmt.order_by(Defect.created_at.desc())).all()
    return [
        {
            "id": defect.id,
            "case_ref": case.ref,
            "title": defect.title,
            "description": defect.description,
            "severity": defect.severity.value,
            "module": defect.module,
            "possible_root_cause": defect.root_cause_ai,
            "recommended_investigation": defect.recommended_fix_ai,
            "status": defect.status,
            "run_id": execution.test_run_id,
            "evidence": {
                "screenshot": ((execution.error_details or {}).get("evidence") or {}).get("screenshot"),
                "error_type": (execution.error_details or {}).get("type"),
            },
        }
        for defect, execution, case in rows
    ]


@router.get("/defects/{defect_id}/evidence")
def defect_evidence(
    defect_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    """Full evidence bundle for a defect (§12 list)."""
    defect = db.execute(
        sa.select(Defect).where(Defect.id == defect_id, Defect.project_id == project.id)
    ).scalar_one_or_none()
    if defect is None or defect.execution_id is None:
        raise HTTPException(status_code=404, detail="Defect not found")

    execution = db.execute(
        sa.select(TestExecution).where(TestExecution.id == defect.execution_id)
    ).scalar_one_or_none()
    case = db.execute(
        sa.select(TestCase).where(TestCase.id == execution.test_case_id)
    ).scalar_one_or_none() if execution else None

    details = (execution.error_details if execution else None) or {}
    evidence = details.get("evidence", {})
    screenshot_key = evidence.get("screenshot") if isinstance(evidence, dict) else None

    screenshot_data = None
    if screenshot_key:
        from app import storage

        try:
            import base64

            screenshot_data = base64.b64encode(storage.get_bytes(screenshot_key)).decode()
        except Exception:
            screenshot_data = None

    return {
        "defect": {
            "id": defect.id,
            "title": defect.title,
            "description": defect.description,
            "severity": defect.severity.value,
            "possible_root_cause": defect.root_cause_ai,
            "recommended_investigation": defect.recommended_fix_ai,
        },
        "case": {"ref": case.ref, "scenario": case.scenario, "steps": case.steps} if case else None,
        "execution": {
            "status": execution.status.value,
            "attempt": execution.attempt,
            "duration_ms": execution.duration_ms,
            "worker_id": execution.worker_id,
            "actual_result": execution.actual_result,
        } if execution else None,
        "evidence": {
            "error_details": {k: v for k, v in details.items() if k != "log"},
            "step_log": details.get("log", []),
            "console": evidence.get("console", []) if isinstance(evidence, dict) else [],
            "screenshot_base64": screenshot_data,
        },
    }
