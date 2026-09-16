"""History & comparison endpoints (spec §21) — Phase 13."""
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.analysis.compare import compare_runs
from app.db import get_db
from app.models import Project, TestExecution, TestRun, User

router = APIRouter(prefix="/api/projects/{project_id}/history", tags=["history"])


@router.get("/runs")
def run_history(
    project: Project = Depends(get_owned_project),
    db=Depends(get_db),
    limit: int = 20,
) -> list[dict]:
    """Execution history with per-run outcome counts (newest first)."""
    runs = db.execute(
        sa.select(TestRun)
        .where(TestRun.project_id == project.id)
        .order_by(TestRun.created_at.desc())
        .limit(min(limit, 50))
    ).scalars().all()

    out = []
    for run in runs:
        counts: dict[str, int] = {"passed": 0, "failed": 0, "skipped": 0, "other": 0}
        rows = db.execute(
            sa.select(TestExecution.status).where(TestExecution.test_run_id == run.id)
        ).all()
        latest: dict[str, str] = {}
        for (status,) in rows:
            # count all execution rows; browser duplicates included
            if status.value in counts:
                counts[status.value] += 1
            else:
                counts["other"] += 1
        out.append(
            {
                "id": run.id,
                "label": run.label,
                "status": run.status.value,
                "browsers": run.browsers or [],
                "created_at": run.created_at.isoformat(),
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                **counts,
            }
        )
    return out


class CompareIn(BaseModel):
    base_run_id: str
    target_run_id: str


@router.post("/compare")
def compare(
    body: CompareIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db=Depends(get_db),
) -> dict:
    # Both runs must belong to this project (authorization + data isolation)
    for run_id in (body.base_run_id, body.target_run_id):
        run = db.execute(
            sa.select(TestRun).where(TestRun.id == run_id, TestRun.project_id == project.id)
        ).scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id[:8]} not found in this project")

    try:
        result = compare_runs(body.base_run_id, body.target_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    audit_record("history.compare", user.id, "project", project.id, body.model_dump())
    return result
