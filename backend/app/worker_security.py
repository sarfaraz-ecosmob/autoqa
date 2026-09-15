"""Security scan worker task (spec §13): run tiered scans in the isolated
security worker queue and persist SecurityFindings."""
import sqlalchemy as sa

from app.db import SessionLocal
from app.models import Project, ScanStatus, SecurityFinding
from app.security.scan import run_scan
from app.tasks import celery_app
from app.worker_tasks import _publish_progress


@celery_app.task(name="app.run_security_scan", bind=True, max_retries=0)
def run_security_scan(
    self,
    scan_id: str,
    progress_channel: str,
    project_id: str,
    base_url: str,
    tier: str = "passive",
    page_paths: list[str] | None = None,
) -> dict:
    db = SessionLocal()
    try:
        project = db.execute(
            sa.select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()
        if project is None:
            return {"scan_id": scan_id, "status": "failed", "error": "project not found"}

        # Destructive-scan guard: full tier requires confirmed authorization
        if tier == "full" and not project.authorization_confirmed:
            return {
                "scan_id": scan_id,
                "status": "failed",
                "error": "Full scan requires confirmed authorization",
            }

        _publish_progress(progress_channel, {"event": "scan_started", "tier": tier})
        report = run_scan(base_url, tier=tier, page_paths=page_paths or [])

        for f in report.findings:
            db.add(
                SecurityFinding(
                    project_id=project_id,
                    scan_id=scan_id,
                    title=f.title,
                    description=f"{f.category}: observed on {f.evidence.get('url', base_url)}",
                    severity=__import__("app.models", fromlist=["Severity"]).Severity(f.severity),
                    evidence=f.evidence,
                    remediation=f.remediation,
                )
            )
        project.settings = {**(project.settings or {}), f"last_security_scan_{tier}": scan_id}
        db.commit()

        result = {
            "scan_id": scan_id,
            "project_id": project_id,
            "tier": tier,
            "status": ScanStatus.completed.value,
            "checked": report.checked,
            "findings": report.counts(),
            "total_findings": len(report.findings),
        }
        _publish_progress(progress_channel, {"event": "scan_completed", **result})
        return result
    except Exception as exc:
        return {"scan_id": scan_id, "status": "failed", "error": str(exc)}
    finally:
        db.close()
