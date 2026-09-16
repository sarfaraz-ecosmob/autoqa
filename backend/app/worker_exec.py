"""Execution worker task (spec §9/§10/§18): run approved test cases.

One task executes ONE test case in ONE browser (or as an API case), updates
TestExecution rows, applies retry policy, publishes real-time events, and
finalizes the TestRun.
"""
import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from app.config import get_settings
from app.db import SessionLocal
from app.models import Artifact, Project, TestExecution, TestCase, TestRun, TestStatus
from app.tasks import celery_app


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@celery_app.task(name="app.execute_test", bind=True, max_retries=0)
def execute_test(
    self,
    execution_id: str,
    test_run_id: str,
    project_id: str,
    base_url: str,
    case: dict,
    variables: dict | None = None,
    browser: str = "chromium",
    attempt: int = 1,
    max_attempts: int = 1,
) -> dict:
    db = SessionLocal()
    worker_id = f"bw-{self.request.hostname}-{self.request.id[:8]}"
    try:
        exec_row = db.execute(
            sa.select(TestExecution).where(TestExecution.id == execution_id)
        ).scalar_one_or_none()
        if exec_row is None:
            return {"execution_id": execution_id, "status": "failed", "error": "execution row missing"}

        exec_row.status = TestStatus.running
        exec_row.started_at = _utcnow()
        exec_row.worker_id = worker_id
        exec_row.attempt = attempt
        db.commit()

        _publish_event(
            test_run_id,
            {
                "event": "test_started",
                "ref": case.get("ref", ""),
                "execution_id": execution_id,
                "browser": browser,
                "attempt": attempt,
                "worker": worker_id,
            },
        )

        started = time_start()
        # §17: resolved test data (env vars + datasets). Secrets live here —
        # never logged, only substituted into {{placeholders}} at execution time.
        resolved_vars = dict(variables or {})
        if case.get("variables"):
            resolved_vars.update(case["variables"])
        if case.get("kind") == "api":
            from app.execution.api_exec import ApiExecutor

            result = ApiExecutor(base_url, variables=resolved_vars).run(case.get("steps", []))
        else:
            from app.execution.browser import BrowserExecutor

            shot_key = f"executions/{execution_id}/screenshot-attempt{attempt}.png"
            result = BrowserExecutor(browser_name=browser, variables=resolved_vars).run(
                case.get("steps", []),
                screenshot_key=shot_key,
                capture_on_pass=bool(case.get("capture_on_pass", False)),
            )

        duration_ms = int((time_end() - started) * 1000)
        final_status = TestStatus(result.get("status", "failed"))

        # Retry policy (spec §18): distinguish retries from original failures
        if final_status == TestStatus.failed and attempt < max_attempts:
            exec_row.status = final_status
            exec_row.finished_at = _utcnow()
            exec_row.duration_ms = duration_ms
            exec_row.actual_result = result.get("actual_result", "")
            exec_row.error_details = result.get("error_details", {})
            db.commit()
            execute_test.apply_async(
                kwargs={
                    "execution_id": execution_id,
                    "test_run_id": test_run_id,
                    "project_id": project_id,
                    "base_url": base_url,
                    "case": case,
                    "variables": resolved_vars,
                    "browser": browser,
                    "attempt": attempt + 1,
                    "max_attempts": max_attempts,
                },
                countdown=2,
            )
            return {"execution_id": execution_id, "status": "failed", "retrying": True, "next_attempt": attempt + 1}

        exec_row.status = final_status
        exec_row.finished_at = _utcnow()
        exec_row.duration_ms = duration_ms
        exec_row.actual_result = result.get("actual_result", "")
        exec_row.error_details = {
            **(result.get("error_details") or {}),
            **({"evidence": result["evidence"]} if result.get("evidence") else {}),
            "log": result.get("log", []),
        }

        # Register screenshot evidence as an Artifact row (§23; feeds §19 reports)
        shot_stored = (result.get("evidence") or {}).get("screenshot")
        if shot_stored:
            db.add(
                Artifact(
                    execution_id=execution_id,
                    kind="screenshot",
                    storage_key=shot_stored,
                    meta={"browser": browser, "attempt": attempt, "ref": case.get("ref", "")},
                )
            )
        db.commit()

        _publish_event(
            test_run_id,
            {
                "event": "test_finished",
                "ref": case.get("ref", ""),
                "execution_id": execution_id,
                "status": final_status.value,
                "attempt": attempt,
                "duration_ms": duration_ms,
                "worker": worker_id,
            },
        )

        _maybe_finalize_run(test_run_id)
        return {"execution_id": execution_id, "status": final_status.value, "attempt": attempt}
    finally:
        db.close()


def _publish_event(test_run_id: str, event: dict) -> None:
    """Publish a run event for the real-time dashboard (SSE)."""
    try:
        from app.tasks import get_redis

        r = get_redis()
        if r is not None:
            from datetime import datetime, timezone

            r.publish(
                f"run:{test_run_id}",
                json.dumps({**event, "ts": datetime.now(timezone.utc).isoformat()}),
            )
    except Exception:
        pass


def _maybe_finalize_run(test_run_id: str) -> None:
    """Mark the run completed when no executions remain queued/running."""
    db = SessionLocal()
    try:
        run = db.execute(sa.select(TestRun).where(TestRun.id == test_run_id)).scalar_one_or_none()
        if run is None or run.status.value not in ("running", "paused"):
            return
        pending = db.execute(
            sa.select(sa.func.count()).select_from(TestExecution).where(
                TestExecution.test_run_id == test_run_id,
                TestExecution.status.in_([TestStatus.queued, TestStatus.running]),
            )
        ).scalar_one()
        if pending == 0:
            run.status = run_status_completed()
            run.finished_at = _utcnow()
            db.commit()
            from app.worker_tasks import _publish_progress

            _publish_progress(f"run:{test_run_id}", {"event": "run_completed"})
            _notify_run_completed(db, run)
    finally:
        db.close()


def _notify_run_completed(db, run) -> None:
    """In-app notification to the project owner (respects their settings)."""
    try:
        from app.models import Project
        from app.notifications import notify_user

        failed = (
            db.execute(
                sa.select(sa.func.count()).select_from(TestExecution).where(
                    TestExecution.test_run_id == run.id,
                    TestExecution.status == TestStatus.failed,
                )
            ).scalar_one()
            or 0
        )
        owner_id = db.execute(sa.select(Project.owner_id).where(Project.id == run.project_id)).scalar_one()
        kind = "run_failed_tests" if failed else "run_completed"
        notify_user(
            db,
            owner_id,
            kind,
            f"Run {run.label or run.id[:8]} completed",
            f"{failed} failed test(s)" if failed else "All tests passed",
            f"/projects/{run.project_id}/runs/{run.id}",
            project_id=str(run.project_id),
        )
    except Exception:
        pass  # notifications must never break run finalization


def run_status_completed():
    from app.models import RunStatus

    return RunStatus.completed


def time_start() -> float:
    import time

    return time.perf_counter()


def time_end() -> float:
    import time

    return time.perf_counter()
