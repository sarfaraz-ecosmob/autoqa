"""Phase 13 unit tests: run comparison engine (spec §21).

Seeds a real project + runs in the test database (docker test DB), verifies
new/resolved/persistent failure diffs, test-set changes, and guards.
"""
import uuid

import pytest
import sqlalchemy as sa

from app.analysis.compare import compare_runs
from app.db import SessionLocal
from app.models import (
    Project,
    RunStatus,
    TestCase,
    TestCategory,
    TestExecution,
    TestRun,
    TestStatus,
    User,
)


def _seed_run(db, project_id: str, case_ids: dict[str, str], outcomes: dict[tuple[str, str], str], label: str) -> str:
    run = TestRun(project_id=project_id, label=label, status=RunStatus.completed, browsers=["chromium"])
    db.add(run)
    db.flush()
    for (ref, browser), status in outcomes.items():
        db.add(
            TestExecution(
                test_run_id=run.id,
                test_case_id=case_ids[ref],
                browser=browser,
                status=TestStatus(status),
                attempt=1,
                duration_ms=1000,
            )
        )
    db.commit()
    return run.id


@pytest.fixture()
def seeded_project():
    db = SessionLocal()
    try:
        user = db.execute(sa.select(User)).scalars().first()
        project = Project(
            owner_id=user.id,
            name=f"cmp-{uuid.uuid4().hex[:8]}",
            base_url="http://demo-app:9000",
            authorization_confirmed=True,
        )
        db.add(project)
        db.flush()
        case_ids = {}
        for ref in ("TC-A", "TC-B", "TC-C"):
            case = TestCase(
                project_id=project.id,
                ref=ref,
                scenario=f"scenario {ref}",
                category=TestCategory.functional,
                steps=[],
            )
            db.add(case)
            db.flush()
            case_ids[ref] = case.id
        db.commit()
        yield db, project.id, case_ids
        # cleanup
        db.execute(sa.delete(TestExecution).where(TestExecution.test_run_id.in_(
            sa.select(TestRun.id).where(TestRun.project_id == project.id))))
        db.execute(sa.delete(TestRun).where(TestRun.project_id == project.id))
        db.execute(sa.delete(TestCase).where(TestCase.project_id == project.id))
        db.execute(sa.delete(Project).where(Project.id == project.id))
        db.commit()
    finally:
        db.close()


def test_compare_diffs_failures_and_test_sets(seeded_project):
    db, project_id, case_ids = seeded_project
    # Base: A fails, B passes; C doesn't exist yet
    base = _seed_run(db, project_id, case_ids, {
        ("TC-A", "chromium"): "failed",
        ("TC-B", "chromium"): "passed",
    }, "Run 1")
    # Target: A fixed, B broken, C added and failing
    target = _seed_run(db, project_id, case_ids, {
        ("TC-A", "chromium"): "passed",
        ("TC-B", "chromium"): "failed",
        ("TC-C", "chromium"): "failed",
    }, "Run 2")

    result = compare_runs(base, target)
    assert {f["ref"] for f in result["new_failures"]} == {"TC-B", "TC-C"}
    assert {f["ref"] for f in result["resolved_failures"]} == {"TC-A"}
    assert result["persistent_failures"] == []
    assert {t["ref"] for t in result["new_tests"]} == {"TC-C"}
    assert result["removed_tests"] == []
    assert result["target"]["failed"] == 2 and result["target"]["passed"] == 1


def test_compare_persistent_failures(seeded_project):
    db, project_id, case_ids = seeded_project
    base = _seed_run(db, project_id, case_ids, {
        ("TC-A", "chromium"): "failed",
        ("TC-B", "chromium"): "passed",
    }, "Run 1")
    target = _seed_run(db, project_id, case_ids, {
        ("TC-A", "chromium"): "failed",
        ("TC-B", "chromium"): "failed",
    }, "Run 2")

    result = compare_runs(base, target)
    assert {f["ref"] for f in result["persistent_failures"]} == {"TC-A"}
    assert {f["ref"] for f in result["new_failures"]} == {"TC-B"}
    assert result["resolved_failures"] == []


def test_compare_swaps_chronology(seeded_project):
    """Base/target are positional: older run is always treated as base."""
    db, project_id, case_ids = seeded_project
    older = _seed_run(db, project_id, case_ids, {("TC-A", "chromium"): "passed"}, "Old")
    newer = _seed_run(db, project_id, case_ids, {("TC-A", "chromium"): "failed"}, "New")

    swapped = compare_runs(newer, older)  # user passed them backwards
    assert swapped["base"]["label"] == "Old"
    assert swapped["target"]["label"] == "New"
    assert {f["ref"] for f in swapped["new_failures"]} == {"TC-A"}


def test_compare_rejects_cross_project(seeded_project):
    db, project_id, case_ids = seeded_project
    other = Project(
        owner_id=db.execute(sa.select(User)).scalars().first().id,
        name=f"cmp-other-{uuid.uuid4().hex[:6]}",
        base_url="http://demo-app:9000",
        authorization_confirmed=True,
    )
    db.add(other)
    db.flush()
    case = TestCase(project_id=other.id, ref="TC-X", scenario="x", category=TestCategory.functional, steps=[])
    db.add(case)
    db.flush()
    run = TestRun(project_id=other.id, label="Other run", status=RunStatus.completed, browsers=["chromium"])
    db.add(run)
    db.flush()
    db.add(TestExecution(test_run_id=run.id, test_case_id=case.id, browser="chromium",
                         status=TestStatus.passed, attempt=1))
    db.commit()

    mine = _seed_run(db, project_id, case_ids, {("TC-A", "chromium"): "passed"}, "Mine")
    with pytest.raises(ValueError, match="different projects"):
        compare_runs(mine, run.id)

    db.execute(sa.delete(TestExecution).where(TestExecution.test_run_id == run.id))
    db.execute(sa.delete(TestRun).where(TestRun.id == run.id))
    db.execute(sa.delete(TestCase).where(TestCase.id == case.id))
    db.execute(sa.delete(Project).where(Project.id == other.id))
    db.commit()


def test_compare_rejects_self(seeded_project):
    db, project_id, case_ids = seeded_project
    run = _seed_run(db, project_id, case_ids, {("TC-A", "chromium"): "passed"}, "Solo")
    with pytest.raises(ValueError, match="itself"):
        compare_runs(run, run)
