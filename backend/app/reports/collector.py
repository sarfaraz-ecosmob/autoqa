"""Report data collection (spec §19) — one evidence-grounded dataset.

Gathers everything the spec requires into a plain dict that every renderer
(CSV/XLSX/PDF/HTML/JSON) consumes. Secrets are never included: project
credentials stay encrypted in the DB and are not read here.
"""
from datetime import datetime, timezone

import sqlalchemy as sa

from app.db import SessionLocal
from app.models import (
    AccessibilityResult,
    ApiEndpoint,
    Application,
    Defect,
    Page,
    PerformanceResult,
    Project,
    Report,
    SecurityFinding,
    TestPlan,
    TestRun,
)


def collect_report_data(project_id: str, test_run_id: str | None = None) -> dict:
    """Build the full §19 dataset for a project (optionally scoped to a run)."""
    db = SessionLocal()
    try:
        project = db.execute(sa.select(Project).where(Project.id == project_id)).scalar_one_or_none()
        if project is None:
            raise ValueError("project not found")

        # ----- Test plan (§6) -----
        plan = db.execute(
            sa.select(TestPlan)
            .where(TestPlan.project_id == project_id)
            .order_by(TestPlan.version.desc())
        ).scalars().first()
        plan_dict = None
        if plan:
            plan_dict = {
                "version": plan.version,
                "objective": plan.objective,
                "scope": plan.scope or {},
                "sections": plan.sections or [],
                "approved": plan.approved,
                "generated_by": plan.generated_by,
            }

        # ----- Test runs / executions -----
        runs_stmt = sa.select(TestRun).where(TestRun.project_id == project_id).order_by(TestRun.created_at.desc())
        if test_run_id:
            runs_stmt = runs_stmt.where(TestRun.id == test_run_id)
        runs = db.execute(runs_stmt).scalars().all()

        from app.models import TestExecution, TestCase, TestStatus

        executions: list[dict] = []
        counters = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "blocked": 0,
                    "running": 0, "queued": 0}
        browsers_used: set[str] = set()

        run_rows = []
        for run in runs:
            rows = db.execute(
                sa.select(TestExecution, TestCase)
                .join(TestCase, TestCase.id == TestExecution.test_case_id)
                .where(TestExecution.test_run_id == run.id)
                .order_by(TestCase.ref)
            ).all()
            run_counts = {"passed": 0, "failed": 0, "skipped": 0, "other": 0}
            run_cases = []
            for ex, case in rows:
                status = ex.status.value
                if status in ("passed", "failed", "skipped", "blocked", "running", "queued"):
                    counters[status] += 1
                counters["total"] += 1
                if status in run_counts:
                    run_counts[status] += 1
                else:
                    run_counts["other"] += 1
                if ex.browser in ("chromium", "firefox", "webkit"):
                    browsers_used.add(ex.browser)
                evidence = (ex.error_details or {}).get("evidence") or {}
                run_cases.append(
                    {
                        "ref": case.ref,
                        "scenario": case.scenario,
                        "module": case.module,
                        "category": case.category.value,
                        "priority": case.priority.value,
                        "kind": case.kind,
                        "browser": ex.browser,
                        "status": status,
                        "attempt": ex.attempt,
                        "duration_ms": ex.duration_ms,
                        "worker_id": ex.worker_id,
                        "actual_result": ex.actual_result,
                        "error_type": (ex.error_details or {}).get("type"),
                        "screenshot_key": evidence.get("screenshot") if isinstance(evidence, dict) else None,
                        "console": (evidence.get("console") or [])[-10:] if isinstance(evidence, dict) else [],
                        "step_log": (ex.error_details or {}).get("log", [])[-15:],
                    }
                )
            executions.extend(run_cases)
            run_rows.append(
                {
                    "id": run.id,
                    "label": run.label,
                    "status": run.status.value,
                    "browsers": run.browsers or [],
                    "created_at": run.created_at.isoformat(),
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                    "cases": run_cases,
                    "counts": run_counts,
                }
            )

        total_done = counters["passed"] + counters["failed"] + counters["skipped"] + counters["blocked"]
        pass_rate = round(counters["passed"] * 100 / total_done, 1) if total_done else 0.0

        # ----- Defects (§12) -----
        defects = db.execute(
            sa.select(Defect).where(Defect.project_id == project_id).order_by(Defect.created_at.desc()).limit(100)
        ).scalars().all()
        defect_rows = [
            {
                "title": d.title,
                "severity": d.severity.value,
                "module": d.module,
                "description": d.description,
                "possible_root_cause": d.root_cause_ai,
                "recommended_investigation": d.recommended_fix_ai,
                "status": d.status,
            }
            for d in defects
        ]

        # ----- Security findings (§13) -----
        findings = db.execute(
            sa.select(SecurityFinding)
            .where(SecurityFinding.project_id == project_id)
            .order_by(SecurityFinding.created_at.desc())
            .limit(100)
        ).scalars().all()
        finding_rows = [
            {
                "title": f.title,
                "severity": f.severity.value,
                "description": f.description,
                "evidence_url": (f.evidence or {}).get("url", "") if isinstance(f.evidence, dict) else "",
                "remediation": f.remediation,
                "status": f.status.value,
            }
            for f in findings
        ]

        # ----- Performance (§14) -----
        perfs = db.execute(
            sa.select(PerformanceResult)
            .where(PerformanceResult.project_id == project_id)
            .order_by(PerformanceResult.created_at.desc())
            .limit(20)
        ).scalars().all()
        perf_rows = [
            {
                "scenario": p.scenario,
                "concurrent_users": p.concurrent_users,
                "p50_ms": p.p50_ms,
                "p90_ms": p.p90_ms,
                "p95_ms": p.p95_ms,
                "p99_ms": p.p99_ms,
                "throughput_rps": p.throughput_rps,
                "error_rate": p.error_rate,
                "threshold_breached": (p.meta or {}).get("threshold_breached"),
                "created_at": p.created_at.isoformat(),
            }
            for p in perfs
        ]

        # ----- Accessibility (§15) -----
        a11y = db.execute(
            sa.select(AccessibilityResult)
            .where(AccessibilityResult.project_id == project_id)
            .order_by(AccessibilityResult.page_url)
        ).scalars().all()
        a11y_rows = []
        for a in a11y:
            by_sev: dict[str, int] = {}
            for v in a.violations or []:
                sev = (v.get("severity") or "minor") if isinstance(v, dict) else "minor"
                by_sev[sev] = by_sev.get(sev, 0) + 1
            a11y_rows.append(
                {
                    "page_url": a.page_url,
                    "violation_count": len(a.violations or []),
                    "by_severity": by_sev,
                    "rules": sorted({(v.get("rule") or "?") for v in a.violations or [] if isinstance(v, dict)}),
                }
            )

        # ----- Application info (§4 discovery) -----
        app = db.execute(
            sa.select(Application).where(Application.project_id == project_id)
        ).scalars().first()
        pages_count = db.execute(
            sa.select(sa.func.count()).select_from(Page).where(Page.project_id == project_id)
        ).scalar_one()
        apis_count = db.execute(
            sa.select(sa.func.count()).select_from(ApiEndpoint).where(ApiEndpoint.project_id == project_id)
        ).scalar_one()

        # ----- Executive summary + recommendations (evidence-grounded) -----
        sev_counts: dict[str, int] = {}
        for d in defect_rows:
            sev_counts[d["severity"]] = sev_counts.get(d["severity"], 0) + 1
        for f in finding_rows:
            sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1
        a11y_total = sum(r["violation_count"] for r in a11y_rows)
        perf_breaches = sum(1 for p in perf_rows if p["threshold_breached"])

        summary_lines = [
            f"Executed {counters['total']} test case executions across {len(run_rows)} run(s)"
            f" and {len(browsers_used) or 1} browser(s).",
            f"Pass rate: {pass_rate}% ({counters['passed']} passed / {counters['failed']} failed"
            f" / {counters['skipped']} skipped).",
            f"{len(defect_rows)} defect(s) triaged; {sum(sev_counts.get(s, 0) for s in ('critical', 'high'))}"
            " critical/high findings across defects and security.",
            f"Accessibility: {a11y_total} violation(s) on {len(a11y_rows)} audited page(s).",
            f"Performance: {len(perf_rows)} smoke run(s), {perf_breaches} threshold breach(es).",
        ]
        recommendations = []
        if counters["failed"]:
            recommendations.append(f"Investigate the {counters['failed']} failed execution(s) — triage and root-cause hypotheses are listed in the Defects section.")
        if sev_counts.get("critical") or sev_counts.get("high"):
            recommendations.append("Address critical/high severity findings first (defects + security).")
        if a11y_total:
            recommendations.append(f"Fix accessibility violations ({a11y_total}) — start with 'critical' severity rules such as form-label and img-alt.")
        if perf_breaches:
            recommendations.append("Performance thresholds breached — review p95 latency against the configured budget.")
        missing_headers = sum(1 for f in finding_rows if "security header" in f["title"].lower())
        if missing_headers:
            recommendations.append(f"Add the {missing_headers} missing security header(s) listed in the Security section (CSP, HSTS, etc.).")
        if not recommendations:
            recommendations.append("No blocking issues detected in the executed scope — keep suites in CI for regression coverage.")

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": {"project_id": project_id, "test_run_id": test_run_id},
            "application": {
                "name": project.name,
                "base_url": project.base_url,
                "authorization_confirmed": project.authorization_confirmed,
                "frontend_stack": (app.frontend_stack if app else {}) or {},
                "backend_stack": (app.backend_stack if app else {}) or {},
                "pages_discovered": pages_count,
                "apis_discovered": apis_count,
            },
            "environment": {
                "platform": "AutoQA Docker Compose",
                "browsers": sorted(browsers_used) or ["chromium"],
                "generated_by_user": "autoqa",
            },
            "test_plan": plan_dict,
            "runs": run_rows,
            "executions": executions,
            "counters": {**counters, "done": total_done, "pass_rate": pass_rate},
            "defects": defect_rows,
            "security_findings": finding_rows,
            "performance": perf_rows,
            "accessibility": a11y_rows,
            "executive_summary": summary_lines,
            "recommendations": recommendations,
        }
    finally:
        db.close()


def get_report_row(report_id: str) -> Report | None:
    db = SessionLocal()
    try:
        return db.execute(sa.select(Report).where(Report.id == report_id)).scalar_one_or_none()
    finally:
        db.close()
