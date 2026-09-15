"""Failure analysis (spec §12).

Analyzes failed executions and records AI-assisted (or heuristic) triage:
Failure Summary, Possible Root Cause, Affected Component, Severity, and
Recommended Investigation. AI output is always framed as POSSIBLE root cause
unless supported by concrete evidence — never stated as confirmed fact.
"""
import json

import sqlalchemy as sa

from app.ai import generate_json
from app.db import SessionLocal
from app.models import Defect, Severity, TestExecution, TestCase

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _heuristic_triage(case: TestCase, execution: TestExecution) -> dict:
    """Evidence-driven triage from the execution's own error details."""
    details = execution.error_details or {}
    etype = details.get("type", "")
    log = details.get("log", [])
    evidence = details.get("evidence", {})
    console = evidence.get("console", []) if isinstance(evidence, dict) else []

    summary = execution.actual_result or "Test failed without a captured message."

    if etype == "status_mismatch":
        expected = details.get("expected")
        actual = details.get("actual")
        if isinstance(actual, int) and actual >= 500:
            root_cause = f"Server error observed (HTTP {actual}) — backend failure during the test flow."
            affected = case.module or "backend API"
            severity = "critical" if case.module in ("orders", "authentication", "checkout") else "high"
            recommended = "Inspect server logs for the failing endpoint; check database connectivity and recent deployments."
        else:
            root_cause = f"Unexpected status code (expected {expected}, got {actual})."
            affected = case.module or "frontend/API contract"
            severity = "medium"
            recommended = "Verify routing rules and API contract for this endpoint."
    elif etype == "server_error_observed":
        root_cause = "A 5xx response was observed during page interaction."
        affected = case.module or "backend"
        severity = "high"
        recommended = "Correlate the failing network request with server-side logs."
    elif etype == "step_error":
        root_cause = "A UI step could not complete — the target element was missing or not interactable."
        affected = case.module or "frontend"
        severity = "medium"
        recommended = "Check whether the page structure changed (selector drift) or a prior step failed silently."
    elif etype == "empty_title":
        root_cause = "Page loaded with an empty title — possibly a blank page or client-side crash."
        affected = "frontend"
        severity = "medium"
        recommended = "Open the evidence screenshot; check browser console errors."
    else:
        root_cause = f"Failure type '{etype or 'unknown'}' — see captured evidence for details."
        affected = case.module or "unknown"
        severity = "medium"
        recommended = "Review the execution log and evidence bundle."

    if console:
        recommended += f" Browser console recorded {len(console)} message(s) — inspect evidence."

    return {
        "summary": summary,
        "root_cause": root_cause,
        "affected": affected,
        "severity": severity,
        "recommended": recommended,
        "evidence": {
            "error_type": etype,
            "log_tail": log[-10:] if isinstance(log, list) else [],
            "console_tail": console[-10:] if isinstance(console, list) else [],
            "screenshot": (evidence or {}).get("screenshot") if isinstance(evidence, dict) else None,
        },
    }


def analyze_failure(execution_id: str) -> dict | None:
    """Triage a failed execution and upsert a Defect. Returns the defect dict."""
    db = SessionLocal()
    try:
        row = db.execute(
            sa.select(TestExecution, TestCase)
            .join(TestCase, TestCase.id == TestExecution.test_case_id)
            .where(TestExecution.id == execution_id)
        ).first()
        if row is None:
            return None
        execution, case = row
        if execution.status.value != "failed":
            return None

        triage = _heuristic_triage(case, execution)

        # Optional LLM refinement, still framed as possible cause
        llm = generate_json(
            system=(
                "You are a QA failure analyst. Given a failed test's evidence, "
                "return JSON keys: summary, possible_root_cause, affected_component, "
                "severity (critical|high|medium|low), recommended_investigation. "
                "The root cause MUST be phrased as a hypothesis, not a confirmed fact."
            ),
            user=json.dumps(
                {
                    "case": {"ref": case.ref, "scenario": case.scenario, "module": case.module},
                    "actual_result": execution.actual_result,
                    "error_details": execution.error_details,
                },
                default=str,
            ),
            fallback={},
        )
        if isinstance(llm, dict) and llm.get("possible_root_cause"):
            triage["root_cause"] = (
                f"Possible root cause (AI): {llm['possible_root_cause']}"
            )
            if llm.get("severity") in _SEVERITY_ORDER:
                triage["severity"] = llm["severity"]
            if llm.get("recommended_investigation"):
                triage["recommended"] = llm["recommended_investigation"]

        # Upsert defect per (execution, attempt)
        existing = db.execute(
            sa.select(Defect).where(Defect.execution_id == execution_id)
        ).scalar_one_or_none()
        if existing:
            defect = existing
        else:
            defect = Defect(
                project_id=case.project_id,
                execution_id=execution_id,
                title=f"{case.ref}: {case.scenario[:120]}",
                severity=Severity(triage["severity"]),
                module=case.module,
            )
            db.add(defect)

        defect.description = triage["summary"]
        defect.root_cause_ai = triage["root_cause"]
        defect.recommended_fix_ai = triage["recommended"]
        db.commit()

        return {
            "defect_id": defect.id,
            "case_ref": case.ref,
            "summary": triage["summary"],
            "possible_root_cause": triage["root_cause"],
            "affected_component": triage["affected"],
            "severity": triage["severity"],
            "recommended_investigation": triage["recommended"],
            "evidence": triage["evidence"],
        }
    finally:
        db.close()


def analyze_run(run_id: str) -> list[dict]:
    """Triage every failed execution in a run."""
    db = SessionLocal()
    try:
        failed_ids = db.execute(
            sa.select(TestExecution.id).where(
                TestExecution.test_run_id == run_id, TestExecution.status == "failed"
            )
        ).scalars().all()
    finally:
        db.close()
    return [d for d in (analyze_failure(eid) for eid in failed_ids) if d]
