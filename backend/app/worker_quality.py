"""Quality module worker tasks (spec §14, §15) — Phase 11.

Runs in the browser-worker image (needs Playwright). Every URL touches the
SSRF guard (§25); performance runs are bounded by PerfConfig hard caps.
"""
import sqlalchemy as sa

from app.db import SessionLocal
from app.models import AccessibilityResult, PerformanceResult, Project
from app.security.ssrf import validate_target_url
from app.tasks import celery_app
from app.worker_tasks import _publish_progress


@celery_app.task(name="app.run_a11y_audit", bind=True, max_retries=0)
def run_a11y_audit(
    self,
    audit_id: str,
    progress_channel: str,
    project_id: str,
    base_url: str,
    page_paths: list[str] | None = None,
) -> dict:
    """Audit discovered pages for WCAG violations (§15) and persist results."""
    db = SessionLocal()
    try:
        project = db.execute(
            sa.select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()
        if project is None:
            return {"audit_id": audit_id, "status": "failed", "error": "project not found"}

        # Defense-in-depth: the API validated already, workers re-check (§25)
        validate_target_url(base_url)

        _publish_progress(progress_channel, {"event": "audit_started"})

        from app.modules.a11y import audit_site

        paths = list(page_paths or [])
        if not paths:
            # Default: discovered pages (capped in audit_site), else base URL
            from app.models import Page

            urls = db.execute(
                sa.select(Page.url).where(Page.project_id == project_id).limit(20)
            ).scalars().all()
            paths = list(urls) or [base_url]

        results = audit_site(paths)

        # Replace previous results so the UI reflects the latest audit
        db.execute(
            sa.delete(AccessibilityResult).where(AccessibilityResult.project_id == project_id)
        )
        total_violations = 0
        for r in results:
            count = len(r.get("violations", []))
            total_violations += count
            db.add(
                AccessibilityResult(
                    project_id=project_id,
                    page_url=r.get("page_url", ""),
                    violations=r.get("violations", []),
                    meta={"violation_count": count},
                )
            )
        project.settings = {**(project.settings or {}), "last_a11y_audit_id": audit_id}
        db.commit()

        result = {
            "audit_id": audit_id,
            "project_id": project_id,
            "status": "completed",
            "pages_audited": len(results),
            "violations": total_violations,
        }
        _publish_progress(progress_channel, {"event": "audit_completed", **result})
        return result
    except Exception as exc:
        return {"audit_id": audit_id, "status": "failed", "error": str(exc)}
    finally:
        db.close()


@celery_app.task(name="app.run_perf_test", bind=True, max_retries=0)
def run_perf_test(
    self,
    perf_id: str,
    progress_channel: str,
    project_id: str,
    base_url: str,
    config: dict,
    test_run_id: str | None = None,
) -> dict:
    """Bounded smoke performance run (§14) persisted to performance_results.

    Safety: SSRF-validated target, rate-bounded, hard request cap (max 200);
    heavier loads require explicit operator configuration (§14 guardrail).
    """
    db = SessionLocal()
    try:
        project = db.execute(
            sa.select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()
        if project is None:
            return {"perf_id": perf_id, "status": "failed", "error": "project not found"}

        _publish_progress(progress_channel, {"event": "perf_started", "path": config.get("path", "/")})

        from app.modules.perf import PerfConfig, run_perf

        # Clamp operator-supplied knobs — smoke-scale by design (§14 guardrail).
        known = set(PerfConfig.__dataclass_fields__)
        cfg = {k: v for k, v in (config or {}).items() if k in known}
        cfg["concurrent_users"] = max(1, min(int(cfg.get("concurrent_users", 2)), 20))
        cfg["duration_seconds"] = max(1, min(int(cfg.get("duration_seconds", 10)), 60))
        cfg["requests_per_second"] = max(0.5, min(float(cfg.get("requests_per_second", 5.0)), 50.0))
        cfg["max_requests"] = max(1, min(int(cfg.get("max_requests", 200)), 1000))
        perf_config = PerfConfig(**cfg)
        result = run_perf(base_url, perf_config)

        db.add(
            PerformanceResult(
                project_id=project_id,
                test_run_id=test_run_id,
                scenario=perf_config.path,
                concurrent_users=result["config"]["concurrent_users"],
                duration_seconds=int(result.get("duration_seconds", 0)),
                p50_ms=result["p50"],
                p90_ms=result["p90"],
                p95_ms=result["p95"],
                p99_ms=result["p99"],
                throughput_rps=result["throughput_rps"],
                error_rate=result["error_rate"],
                meta={
                    "avg_ms": result["avg"],
                    "threshold_breached": result["threshold_breached"],
                    "max_response_time_ms": perf_config.max_response_time_ms,
                    "total_requests": result["total_requests"],
                    "errors": result["errors"],
                    "requests_per_second": perf_config.requests_per_second,
                },
            )
        )
        project.settings = {**(project.settings or {}), "last_perf_run_id": perf_id}
        db.commit()

        result.update(
            {
                "perf_id": perf_id,
                "project_id": project_id,
                "status": "completed",
            }
        )
        _publish_progress(progress_channel, {"event": "perf_completed", "p95": result["p95"]})
        return result
    except Exception as exc:
        return {"perf_id": perf_id, "status": "failed", "error": str(exc)}
    finally:
        db.close()
