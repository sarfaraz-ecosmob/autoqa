"""Real-time execution endpoints (spec §10, §11).

- SSE stream of run events (workers publish progress to Redis pub/sub)
- Worker registry heartbeat + status for the worker visibility panel
- Live log tail per run
"""
import json

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_project
from app.db import get_db
from app.models import Project, TestExecution, TestCase, TestRun, TestStatus, Worker
from app.tasks import get_redis

router = APIRouter(prefix="/api/projects/{project_id}", tags=["realtime"])


@router.get("/test-runs/{run_id}/stream")
def run_stream(
    run_id: str,
    project: Project = Depends(get_owned_project),
):
    """SSE stream of run events: progress snapshots + per-test lifecycle events."""
    from app.models import Project

    def event_gen():
        r = get_redis()
        if r is None:
            yield "data: {\"error\": \"redis unavailable\"}\n\n"
            return
        pubsub = r.pubsub()
        pubsub.subscribe(f"run:{run_id}")
        heartbeat = 0
        for message in pubsub.listen():
            heartbeat += 1
            if message["type"] == "message":
                yield f"data: {message['data'].decode()}\n\n"
            elif heartbeat % 15 == 0:
                yield ": keepalive\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/test-runs/{run_id}/live")
def run_live(
    run_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    """Polling fallback: current counters + latest executions (with logs)."""
    run = db.execute(
        sa.select(TestRun).where(TestRun.id == run_id, TestRun.project_id == project.id)
    ).scalar_one_or_none()
    if run is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Run not found")

    counts: dict[str, int] = {}
    for status in TestStatus:
        counts[status.value] = db.execute(
            sa.select(sa.func.count()).select_from(TestExecution).where(
                TestExecution.test_run_id == run_id, TestExecution.status == status
            )
        ).scalar_one()
    total = sum(counts.values())
    done = total - counts.get("queued", 0) - counts.get("running", 0)

    latest = db.execute(
        sa.select(TestExecution, TestCase)
        .join(TestCase, TestCase.id == TestExecution.test_case_id)
        .where(TestExecution.test_run_id == run_id)
        .order_by(TestExecution.updated_at.desc())
        .limit(20)
    ).all()
    return {
        "status": run.status.value,
        "counters": {**counts, "total": total, "done": done,
                     "percent": int(done * 100 / total) if total else 0},
        "recent": [
            {
                "execution_id": ex.id,
                "ref": case.ref,
                "status": ex.status.value,
                "attempt": ex.attempt,
                "browser": ex.browser,
                "duration_ms": ex.duration_ms,
                "actual_result": ex.actual_result[:200],
                "log": (ex.error_details or {}).get("log", [])[:10],
                "updated_at": ex.updated_at.isoformat(),
            }
            for ex, case in latest
        ],
    }


@router.post("/workers/heartbeat")
def worker_heartbeat(
    body: dict,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict:
    """Workers register/refresh their status here (spec §11 worker panel)."""
    from datetime import datetime, timezone

    key = body.get("worker_key", "")
    if not key:
        return {"ok": False}
    row = db.execute(sa.select(Worker).where(Worker.worker_key == key)).scalar_one_or_none()
    if row is None:
        row = Worker(worker_key=key)
        db.add(row)
    row.kind = body.get("kind", row.kind)
    row.status = body.get("status", row.status)
    row.browser = body.get("browser", row.browser)
    row.stats = {
        "current_test": body.get("current_test", ""),
        "cpu": body.get("cpu"),
        "memory": body.get("memory"),
        "queue_size": body.get("queue_size"),
        "elapsed": body.get("elapsed"),
        "seen_at": datetime.now(timezone.utc).isoformat(),
    }
    row.current_execution_id = body.get("current_execution_id")
    db.commit()
    return {"ok": True}


@router.get("/workers")
def list_workers(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(sa.select(Worker).order_by(Worker.worker_key)).scalars()
    return [
        {
            "id": w.id,
            "worker_key": w.worker_key,
            "kind": w.kind,
            "status": w.status,
            "browser": w.browser,
            "current_test": (w.stats or {}).get("current_test", ""),
            "stats": w.stats,
        }
        for w in rows
    ]
