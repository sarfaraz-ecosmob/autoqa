"""Report generation worker task (spec §19) — Phase 12.

Background job: collect §19 data → render in the requested format → store in
artifact storage → update the Report row (status + storage_key + meta).
"""
import sqlalchemy as sa

from app.db import SessionLocal
from app.models import Report
from app.tasks import celery_app
from app.worker_tasks import _publish_progress


@celery_app.task(name="app.generate_report", bind=True, max_retries=0)
def generate_report(
    self,
    report_id: str,
    project_id: str,
    fmt: str,
    test_run_id: str | None = None,
    progress_channel: str | None = None,
) -> dict:
    db = SessionLocal()
    try:
        row = db.execute(sa.select(Report).where(Report.id == report_id)).scalar_one_or_none()
        if row is None:
            return {"report_id": report_id, "status": "failed", "error": "report row missing"}
        if row.project_id != project_id:
            return {"report_id": report_id, "status": "failed", "error": "project mismatch"}

        from app.reports.collector import collect_report_data
        from app.reports.renderers import RENDERERS

        renderer = RENDERERS.get(fmt)
        if renderer is None:
            row.status = "failed"
            db.commit()
            return {"report_id": report_id, "status": "failed", "error": f"unknown format: {fmt}"}

        data = collect_report_data(project_id, test_run_id)
        content, content_type = renderer(data)

        storage_key = f"reports/{report_id}.{fmt}"
        from app import storage

        storage.put_bytes(storage_key, content, content_type)

        row.status = "completed"
        row.storage_key = storage_key
        row.meta = {
            "size_bytes": len(content),
            "content_type": content_type,
            "scope": "run" if test_run_id else "project",
            "executions": data["counters"]["total"],
            "failed": data["counters"]["failed"],
            "pass_rate": data["counters"]["pass_rate"],
        }
        db.commit()

        if progress_channel:
            _publish_progress(progress_channel, {"event": "report_completed", "report_id": report_id})

        return {
            "report_id": report_id,
            "status": "completed",
            "storage_key": storage_key,
            "size_bytes": len(content),
        }
    except Exception as exc:
        db = SessionLocal()
        try:
            row = db.execute(sa.select(Report).where(Report.id == report_id)).scalar_one_or_none()
            if row is not None:
                row.status = "failed"
                row.meta = {"error": str(exc)[:500]}
                db.commit()
        finally:
            db.close()
        return {"report_id": report_id, "status": "failed", "error": str(exc)}
    finally:
        db.close()
