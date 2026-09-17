"""Shared run-dispatch service (used by manual start AND scheduled runs).

Parallelism (Tier-1): instead of dispatching every execution to the queue at
once, at most `parallelism` executions are in flight; the execution worker
tops the run up as each test finishes (see worker_exec._top_up_run). This
gives run-level concurrency control without changing the queue topology.
"""
import sqlalchemy as sa

from app.models import Project, TestCase, TestExecution, TestRun, TestStatus


def dispatch_run(project: Project, run: TestRun, parallelism: int | None = None, db=None) -> int:
    """Send queued executions of `run` to the browser queue, up to the
    parallelism cap (unlimited when parallelism <= 0 or None). Returns the
    number dispatched."""
    from app.tasks import celery_app
    from app.testdata import resolve_variables

    own_session = db is None
    if own_session:
        from app.db import SessionLocal

        db = SessionLocal()
    try:
        executions = db.execute(
            sa.select(TestExecution).where(
                TestExecution.test_run_id == run.id,
                TestExecution.status == TestStatus.queued,
            )
        ).scalars().all()

        max_retries = int((run.config or {}).get("max_retries", 0))
        if parallelism is not None and parallelism > 0:
            in_flight = db.execute(
                sa.select(sa.func.count()).select_from(TestExecution).where(
                    TestExecution.test_run_id == run.id,
                    TestExecution.status == TestStatus.running,
                )
            ).scalar_one()
            budget = max(0, parallelism - int(in_flight))
        else:
            budget = len(executions)
        batch = executions[:budget]

        try:
            variables = resolve_variables(project, (run.config or {}).get("environment_id"))
        except Exception:
            variables = {}

        # Authenticated-target support: project credentials (decrypted only
        # here, inside the execution path) + the project's auth flow config.
        credentials: dict = {}
        auth: dict = {}
        if project.credentials_encrypted:
            try:
                from app.security.crypto import decrypt_json

                blob = decrypt_json(project.credentials_encrypted)
                credentials = blob.get("values", {}) or {}
                auth = blob.get("auth", {}) or {}
            except Exception:
                credentials, auth = {}, {}

        dispatched = 0
        for ex in batch:
            case = db.execute(
                sa.select(TestCase).where(TestCase.id == ex.test_case_id)
            ).scalar_one_or_none()
            if case is None:
                continue
            celery_app.send_task(
                "app.execute_test",
                kwargs={
                    "execution_id": ex.id,
                    "test_run_id": run.id,
                    "project_id": project.id,
                    "base_url": project.base_url,
                    "case": {
                        "kind": case.kind,
                        "steps": case.steps,
                        "ref": case.ref,
                        "scenario": case.scenario,
                        "capture_on_pass": case.capture_on_pass,
                    },
                    "variables": variables,
                    "credentials": credentials,
                    "auth": auth,
                    "browser": ex.browser,
                    "attempt": 1,
                    "max_attempts": max_retries + 1,
                    "parallelism": parallelism or 0,
                },
                task_id=f"exec-{ex.id}",
            )
            dispatched += 1
        return dispatched
    finally:
        if own_session:
            db.close()
