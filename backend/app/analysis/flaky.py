"""Flaky test detection (BrowserStack Observability / Katalon TestOps style).

A test execution is flaky when its outcome is unstable across attempts or
runs. Signals we compute from data already stored:

- retried-pass:      failed on attempt 1, passed on a later attempt (the
                     classic retry-masks-flakiness smell)
- alternating:       both passes and failures across recent runs (excluding
                     the most recent outcome, so "currently green after a
                     fix" tests don't score high forever)
- pass-rate drift:   outcome rate far from 0/1 across many runs

Score is 0..100; tests are classified stable / suspect / flaky. Terminal
statuses (skipped/blocked) are ignored — they say nothing about stability.
"""
import sqlalchemy as sa

from app.db import SessionLocal
from app.models import TestExecution, TestRun, TestStatus

TERMINAL_IRRELEVANT = {TestStatus.skipped, TestStatus.blocked}
WINDOW = 20  # recent runs considered per test case


def analyze_project(db, project_id: str) -> dict:
    """Flakiness report for all test cases with execution history."""
    rows = db.execute(
        sa.select(TestExecution, TestRun.id)
        .join(TestRun, TestRun.id == TestExecution.test_run_id)
        .where(TestRun.project_id == project_id)
        .order_by(TestRun.created_at.desc(), TestExecution.attempt.asc())
        .limit(5000)
    ).all()

    per_case: dict[str, dict] = {}
    for ex, run_id in rows:
        if ex.status in TERMINAL_IRRELEVANT:
            continue
        entry = per_case.setdefault(ex.test_case_id, {"attempts": [], "runs": {}})
        entry["attempts"].append(ex.attempt)
        entry["runs"].setdefault(run_id, []).append(ex.status.value)

    items = []
    for case_id, data in per_case.items():
        runs = data["runs"]  # run_id -> [status per attempt, newest run first]
        outcomes: list[str] = []
        retried_pass = False
        for statuses in runs.values():
            if len(statuses) > 1 and "failed" in statuses and "passed" in statuses:
                retried_pass = True  # failed then passed within one run's retries
            outcomes.append(statuses[0])  # first-attempt outcome per run

        recent = outcomes[:WINDOW]
        total = len(recent)
        if total == 0:
            continue
        passes = recent.count("passed")
        fails = recent.count("failed")
        pass_rate = passes / total

        # alternating: both outcomes present among runs *before* the latest
        older = recent[1:]
        alternating = bool(older) and "passed" in older and "failed" in older

        score = 0
        if retried_pass:
            score += 40
        if alternating:
            score += 35
        # instability of first-attempt outcomes across the window
        if 0.0 < pass_rate < 1.0 and total >= 3:
            score += int(round(25 * (1 - abs(pass_rate * 2 - 1))))
        score = min(score, 100)

        label = "flaky" if score >= 60 else ("suspect" if score >= 30 else "stable")
        items.append(
            {
                "test_case_id": case_id,
                "runs_observed": total,
                "pass_rate": round(pass_rate, 3),
                "retried_pass": retried_pass,
                "alternating": alternating,
                "flaky_score": score,
                "classification": label,
            }
        )

    items.sort(key=lambda i: (-i["flaky_score"], i["test_case_id"]))
    summary = {
        "flaky": sum(1 for i in items if i["classification"] == "flaky"),
        "suspect": sum(1 for i in items if i["classification"] == "suspect"),
        "stable": sum(1 for i in items if i["classification"] == "stable"),
    }
    return {"summary": summary, "items": items}


def analyze_case(db, project_id: str, test_case_id: str) -> dict | None:
    """Single-case detail (used by the UI drill-down)."""
    report = analyze_project(db, project_id)
    for item in report["items"]:
        if item["test_case_id"] == test_case_id:
            return item
    return None
