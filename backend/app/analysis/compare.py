"""Run history & comparison (spec §21) — Phase 13.

Diffs two runs of the same project: new failures, resolved failures,
persistent failures, new/removed tests, and performance changes.
Outcomes are compared per (test ref, browser) using the latest attempt.
"""
import sqlalchemy as sa

from app.db import SessionLocal
from app.models import PerformanceResult, TestCase, TestExecution, TestRun

_TERMINAL = {"passed", "failed", "skipped", "blocked"}


def _outcomes(db, run_id: str) -> dict[tuple[str, str], dict]:
    """Latest outcome per (ref, browser) for a run."""
    rows = db.execute(
        sa.select(TestExecution, TestCase)
        .join(TestCase, TestCase.id == TestExecution.test_case_id)
        .where(TestExecution.test_run_id == run_id)
        .order_by(TestExecution.attempt)
    ).all()
    outcomes: dict[tuple[str, str], dict] = {}
    for ex, case in rows:
        status = ex.status.value
        if status in _TERMINAL or status not in ("queued", "running"):
            outcomes[(case.ref, ex.browser)] = {
                "status": status,
                "duration_ms": ex.duration_ms,
                "error_type": (ex.error_details or {}).get("type"),
            }
        else:
            outcomes[(case.ref, ex.browser)] = {
                "status": status,
                "duration_ms": None,
                "error_type": None,
            }
    return outcomes


def compare_runs(base_run_id: str, target_run_id: str) -> dict:
    """Diff base (older) vs target (newer) run. Returns the §21 dataset."""
    if base_run_id == target_run_id:
        raise ValueError("cannot compare a run with itself")

    db = SessionLocal()
    try:
        base = db.execute(sa.select(TestRun).where(TestRun.id == base_run_id)).scalar_one_or_none()
        target = db.execute(sa.select(TestRun).where(TestRun.id == target_run_id)).scalar_one_or_none()
        if base is None or target is None:
            raise ValueError("run not found")
        if base.project_id != target.project_id:
            raise ValueError("runs belong to different projects")
        project_id = base.project_id

        # Chronology guard: base must be the older run
        if target.created_at < base.created_at:
            base, target = target, base
            base_run_id, target_run_id = target_run_id, base_run_id

        base_out = _outcomes(db, base_run_id)
        target_out = _outcomes(db, target_run_id)
        base_keys = set(base_out)
        target_keys = set(target_out)

        def _fails(outcomes) -> set:
            return {k for k, v in outcomes.items() if v["status"] == "failed"}

        base_fails, target_fails = _fails(base_out), _fails(target_out)

        new_failures = sorted(target_fails - base_fails)
        resolved = sorted(base_fails - target_fails)
        persistent = sorted(base_fails & target_fails)

        new_tests = sorted(target_keys - base_keys)
        removed_tests = sorted(base_keys - target_keys)

        # Duration deltas for tests present in both runs
        duration_changes = []
        for key in sorted(base_keys & target_keys):
            b, t = base_out[key], target_out[key]
            if b["duration_ms"] and t["duration_ms"]:
                delta = t["duration_ms"] - b["duration_ms"]
                if abs(delta) > 250:  # ignore noise below 250ms
                    duration_changes.append(
                        {
                            "ref": key[0],
                            "browser": key[1],
                            "base_ms": b["duration_ms"],
                            "target_ms": t["duration_ms"],
                            "delta_ms": delta,
                        }
                    )
        duration_changes.sort(key=lambda d: d["delta_ms"])

        # Performance changes for this project between the two run timestamps
        perf_changes = []
        perfs = db.execute(
            sa.select(PerformanceResult)
            .where(
                PerformanceResult.project_id == project_id,
                PerformanceResult.created_at >= base.created_at,
            )
            .order_by(PerformanceResult.created_at)
        ).scalars().all()
        by_scenario: dict[str, list] = {}
        for p in perfs:
            by_scenario.setdefault(p.scenario, []).append(p)
        for scenario, items in by_scenario.items():
            if len(items) >= 2:
                first, last = items[0], items[-1]
                perf_changes.append(
                    {
                        "scenario": scenario,
                        "base_p95_ms": first.p95_ms,
                        "target_p95_ms": last.p95_ms,
                        "delta_p95_ms": round(last.p95_ms - first.p95_ms, 1),
                        "base_throughput_rps": first.throughput_rps,
                        "target_throughput_rps": last.throughput_rps,
                    }
                )

        # Run summaries
        def _summary(run_id: str, outcomes) -> dict:
            statuses = [v["status"] for v in outcomes.values()]
            return {
                "run_id": run_id,
                "label": (base if run_id == base_run_id else target).label,
                "total": len(statuses),
                "passed": statuses.count("passed"),
                "failed": statuses.count("failed"),
                "skipped": statuses.count("skipped"),
            }

        return {
            "project_id": project_id,
            "base": _summary(base_run_id, base_out),
            "target": _summary(target_run_id, target_out),
            "new_failures": [{"ref": k[0], "browser": k[1]} for k in new_failures],
            "resolved_failures": [{"ref": k[0], "browser": k[1]} for k in resolved],
            "persistent_failures": [{"ref": k[0], "browser": k[1]} for k in persistent],
            "new_tests": [{"ref": k[0], "browser": k[1]} for k in new_tests],
            "removed_tests": [{"ref": k[0], "browser": k[1]} for k in removed_tests],
            "duration_changes": duration_changes[:25],
            "performance_changes": perf_changes,
        }
    finally:
        db.close()
