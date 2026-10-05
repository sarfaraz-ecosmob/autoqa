"""Test suites (saved groups of test cases): CRUD + run selection by suite.

Uses the repo's isolation pattern (minimal FastAPI app with the relevant
routers + SQLite via dependency overrides) — no Playwright, no Celery.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def env():
    """Isolated app (suites + test-runs routers) with one owned project."""
    from fastapi import FastAPI

    from app.api import suites as suites_router
    from app.api import testruns as testruns_router
    from app.api.deps import get_current_user, get_db
    from app.models import Base, Project, Role, User

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    user = User(email="suites-test@example.com", password_hash="x", role=Role.admin)
    db.add(user)
    db.commit()
    project = Project(
        owner_id=user.id,
        name="SuitesProj",
        base_url="http://demo-app:9000",
        authorization_confirmed=True,
        settings={},
    )
    db.add(project)
    db.commit()
    pid = project.id

    app = FastAPI()
    app.include_router(suites_router.router)
    app.include_router(testruns_router.router)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: user

    yield {"client": TestClient(app), "db": db, "pid": pid}

    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def _mkcase(env, ref, priority="medium", kind="browser", approved=True, enabled=True, test_plan_id=None, category="functional"):
    from app.models import Priority, TestCase, TestCategory

    case = TestCase(
        project_id=env["pid"],
        ref=ref,
        scenario=f"Scenario {ref}",
        module="general",
        priority=Priority(priority),
        category=TestCategory(category),
        kind=kind,
        steps=[],
        test_plan_id=test_plan_id,
        approved=approved,
        enabled=enabled,
    )
    env["db"].add(case)
    env["db"].commit()
    return case


def _mkplan(env, version):
    from app.models import TestPlan

    plan = TestPlan(project_id=env["pid"], version=version, objective=f"plan {version}", sections=[])
    env["db"].add(plan)
    env["db"].commit()
    return plan


# ---------- CRUD ----------

def test_create_list_get_delete_suite(env):
    c, pid = env["client"], env["pid"]
    _mkcase(env, "TC-A-001")
    _mkcase(env, "TC-A-002")

    res = c.post(f"/api/projects/{pid}/suites", json={"name": "Checkout flows", "refs": ["TC-A-001", "TC-A-002"]})
    assert res.status_code == 201, res.text
    suite = res.json()
    assert suite["name"] == "Checkout flows"
    assert suite["test_case_ids"] == ["TC-A-001", "TC-A-002"]
    assert suite["case_count"] == 2

    listed = c.get(f"/api/projects/{pid}/suites").json()
    assert [s["name"] for s in listed] == ["Checkout flows"]

    got = c.get(f"/api/projects/{pid}/suites/{suite['id']}").json()
    assert got["id"] == suite["id"]

    res = c.delete(f"/api/projects/{pid}/suites/{suite['id']}")
    assert res.status_code == 204
    assert c.get(f"/api/projects/{pid}/suites").json() == []


def test_duplicate_suite_name_rejected(env):
    c, pid = env["client"], env["pid"]
    _mkcase(env, "TC-B-001")
    assert c.post(f"/api/projects/{pid}/suites", json={"name": "smoke", "refs": ["TC-B-001"]}).status_code == 201
    assert c.post(f"/api/projects/{pid}/suites", json={"name": "smoke", "refs": []}).status_code == 409


def test_unknown_refs_rejected(env):
    c, pid = env["client"], env["pid"]
    res = c.post(f"/api/projects/{pid}/suites", json={"name": "bad", "refs": ["TC-NOPE-999"]})
    assert res.status_code == 422
    assert "TC-NOPE-999" in res.json()["detail"]


def test_update_suite_name_and_refs(env):
    c, pid = env["client"], env["pid"]
    _mkcase(env, "TC-C-001")
    _mkcase(env, "TC-C-002")
    suite = c.post(f"/api/projects/{pid}/suites", json={"name": "Old", "refs": ["TC-C-001"]}).json()

    res = c.patch(f"/api/projects/{pid}/suites/{suite['id']}", json={"name": "New", "refs": ["TC-C-002"]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "New"
    assert body["test_case_ids"] == ["TC-C-002"]


# ---------- run selection by suite ----------

def test_run_by_suite_id_only_runs_group_cases(env):
    import sqlalchemy as sa

    from app.models import TestCase, TestExecution

    c, pid = env["client"], env["pid"]
    _mkcase(env, "TC-R-001")
    _mkcase(env, "TC-R-002")
    _mkcase(env, "TC-R-003")
    suite = c.post(
        f"/api/projects/{pid}/suites",
        json={"name": "nightly", "refs": ["TC-R-001", "TC-R-003"]},
    ).json()

    run = c.post(
        f"/api/projects/{pid}/test-runs",
        json={"browsers": ["chromium"], "suite_id": suite["id"]},
    )
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]
    assert run.json()["config"]["suite_id"] == suite["id"]

    exec_case_ids = set(
        env["db"].execute(
            sa.select(TestExecution.test_case_id).where(TestExecution.test_run_id == run_id)
        ).scalars().all()
    )
    expected_ids = {
        row[0]
        for row in env["db"].execute(
            sa.select(TestCase.id, TestCase.ref).where(TestCase.project_id == pid)
        ).all()
        if row[1] in ("TC-R-001", "TC-R-003")
    }
    assert exec_case_ids == expected_ids  # TC-R-002 must not be in the run


def test_run_with_unknown_suite_id_404(env):
    c, pid = env["client"], env["pid"]
    _mkcase(env, "TC-D-001")
    res = c.post(f"/api/projects/{pid}/test-runs", json={"suite_id": "no-such-suite"})
    assert res.status_code == 404


# ---------- test-plan pinning ----------

def test_suite_plan_pin_roundtrip_and_plan_versions(env):
    c, pid = env["client"], env["pid"]
    _mkcase(env, "TC-P-001")
    plan_v1 = _mkplan(env, 1)
    plan_v2 = _mkplan(env, 2)

    versions = c.get(f"/api/projects/{pid}/suites/plan-versions").json()
    assert [v["version"] for v in versions] == [2, 1]  # newest first

    suite = c.post(
        f"/api/projects/{pid}/suites",
        json={"name": "pinned", "refs": ["TC-P-001"], "test_plan_id": plan_v2.id},
    ).json()
    assert suite["test_plan_id"] == plan_v2.id
    assert suite["test_plan_label"] == "v2"

    # unknown plan id rejected
    assert c.post(f"/api/projects/{pid}/suites", json={"name": "x", "test_plan_id": "nope"}).status_code == 404

    # clear the pin via unset_test_plan
    upd = c.patch(f"/api/projects/{pid}/suites/{suite['id']}", json={"unset_test_plan": True}).json()
    assert upd["test_plan_id"] is None
    assert plan_v1.id  # silence unused-var linters


def test_run_by_suite_respects_plan_pin(env):
    c, pid = env["client"], env["pid"]
    plan_v1 = _mkplan(env, 1)
    plan_v2 = _mkplan(env, 2)
    _mkcase(env, "TC-PP-V1", test_plan_id=plan_v1.id)
    _mkcase(env, "TC-PP-V2A", test_plan_id=plan_v2.id)
    _mkcase(env, "TC-PP-V2B", test_plan_id=plan_v2.id)

    # suite refs span both plans, but the suite is pinned to v2
    suite = c.post(
        f"/api/projects/{pid}/suites",
        json={"name": "v2 only", "refs": ["TC-PP-V1", "TC-PP-V2A", "TC-PP-V2B"], "test_plan_id": plan_v2.id},
    ).json()

    run = c.post(f"/api/projects/{pid}/test-runs", json={"suite_id": suite["id"]})
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]

    import sqlalchemy as sa

    from app.models import TestCase, TestExecution

    refs = {
        row[0]
        for row in env["db"].execute(
            sa.select(TestCase.ref).join(TestExecution, TestExecution.test_case_id == TestCase.id)
            .where(TestExecution.test_run_id == run_id)
        ).all()
    }
    assert refs == {"TC-PP-V2A", "TC-PP-V2B"}  # v1 case excluded by the plan pin


def test_run_with_explicit_test_plan_id_filter(env):
    c, pid = env["client"], env["pid"]
    plan_v1 = _mkplan(env, 1)
    plan_v2 = _mkplan(env, 2)
    _mkcase(env, "TC-EF-V1", test_plan_id=plan_v1.id)
    _mkcase(env, "TC-EF-V2", test_plan_id=plan_v2.id)

    run = c.post(f"/api/projects/{pid}/test-runs", json={"test_plan_id": plan_v1.id})
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]
    assert run.json()["config"]["test_plan_id"] == plan_v1.id

    import sqlalchemy as sa

    from app.models import TestCase, TestExecution

    refs = {
        row[0]
        for row in env["db"].execute(
            sa.select(TestCase.ref).join(TestExecution, TestExecution.test_case_id == TestCase.id)
            .where(TestExecution.test_run_id == run_id)
        ).all()
    }
    assert refs == {"TC-EF-V1"}


def test_run_with_plan_without_cases_409(env):
    c, pid = env["client"], env["pid"]
    plan = _mkplan(env, 1)
    _mkcase(env, "TC-NP-001")  # no test_plan_id
    res = c.post(f"/api/projects/{pid}/test-runs", json={"test_plan_id": plan.id})
    assert res.status_code == 409
    assert "v1" in res.json()["detail"]


# ---------- test-type classification ----------

def test_suite_test_types_roundtrip_and_validation(env):
    c, pid = env["client"], env["pid"]
    _mkcase(env, "TC-TT-001")

    suite = c.post(
        f"/api/projects/{pid}/suites",
        json={"name": "auth checks", "refs": ["TC-TT-001"], "test_types": ["functional", "security"]},
    ).json()
    assert suite["test_types"] == ["functional", "security"]

    # update: replace types, then clear with []
    upd = c.patch(
        f"/api/projects/{pid}/suites/{suite['id']}", json={"test_types": ["api"]}
    ).json()
    assert upd["test_types"] == ["api"]
    upd = c.patch(
        f"/api/projects/{pid}/suites/{suite['id']}", json={"test_types": []}
    ).json()
    assert upd["test_types"] == []

    # unknown type rejected
    res = c.post(f"/api/projects/{pid}/suites", json={"name": "bad", "test_types": ["load-test"]})
    assert res.status_code == 422
    assert "load-test" in res.json()["detail"]


def test_run_by_suite_respects_test_types(env):
    c, pid = env["client"], env["pid"]
    _mkcase(env, "TC-TT-FUNC")
    _mkcase(env, "TC-TT-SEC", category="security")
    _mkcase(env, "TC-TT-API", kind="api", category="api")

    suite = c.post(
        f"/api/projects/{pid}/suites",
        json={
            "name": "security only",
            "refs": ["TC-TT-FUNC", "TC-TT-SEC", "TC-TT-API"],
            "test_types": ["security"],
        },
    ).json()

    run = c.post(f"/api/projects/{pid}/test-runs", json={"suite_id": suite["id"]})
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]

    import sqlalchemy as sa

    from app.models import TestCase, TestExecution

    refs = {
        row[0]
        for row in env["db"].execute(
            sa.select(TestCase.ref).join(TestExecution, TestExecution.test_case_id == TestCase.id)
            .where(TestExecution.test_run_id == run_id)
        ).all()
    }
    assert refs == {"TC-TT-SEC"}


def test_run_with_adhoc_test_type_filter(env):
    c, pid = env["client"], env["pid"]
    _mkcase(env, "TC-AD-FUNC")
    _mkcase(env, "TC-AD-API", kind="api", category="api")

    run = c.post(
        f"/api/projects/{pid}/test-runs",
        json={"test_types": ["functional"]},
    )
    assert run.status_code == 201, run.text
    assert run.json()["config"]["test_types"] == ["functional"]
    run_id = run.json()["id"]

    import sqlalchemy as sa

    from app.models import TestCase, TestExecution

    refs = {
        row[0]
        for row in env["db"].execute(
            sa.select(TestCase.ref).join(TestExecution, TestExecution.test_case_id == TestCase.id)
            .where(TestExecution.test_run_id == run_id)
        ).all()
    }
    assert refs == {"TC-AD-FUNC"}


def test_run_suite_types_win_over_adhoc_filter(env):
    """A group's declared test types override the request's ad-hoc filter."""
    c, pid = env["client"], env["pid"]
    _mkcase(env, "TC-W-FUNC")
    _mkcase(env, "TC-W-SEC", category="security")

    suite = c.post(
        f"/api/projects/{pid}/suites",
        json={"name": "sec", "refs": ["TC-W-FUNC", "TC-W-SEC"], "test_types": ["security"]},
    ).json()

    run = c.post(
        f"/api/projects/{pid}/test-runs",
        json={"suite_id": suite["id"], "test_types": ["functional"]},
    )
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]

    import sqlalchemy as sa

    from app.models import TestCase, TestExecution

    refs = {
        row[0]
        for row in env["db"].execute(
            sa.select(TestCase.ref).join(TestExecution, TestExecution.test_case_id == TestCase.id)
            .where(TestExecution.test_run_id == run_id)
        ).all()
    }
    assert refs == {"TC-W-SEC"}  # security won, functional case excluded


def test_run_with_unknown_test_type_422(env):
    c, pid = env["client"], env["pid"]
    _mkcase(env, "TC-UT-001")
    res = c.post(f"/api/projects/{pid}/test-runs", json={"test_types": ["load-test"]})
    assert res.status_code == 422
    assert "load-test" in res.json()["detail"]


def test_run_by_legacy_suite_name_still_works(env):
    c, pid = env["client"], env["pid"]
    _mkcase(env, "TC-E-001")
    _mkcase(env, "TC-E-002")
    c.post(f"/api/projects/{pid}/suites", json={"name": "smoke", "refs": ["TC-E-001"]})

    run = c.post(f"/api/projects/{pid}/test-runs", json={"suite": "smoke"})
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]
    import sqlalchemy as sa

    from app.models import TestCase, TestExecution

    refs = {
        row[0]
        for row in env["db"].execute(
            sa.select(TestCase.ref).join(TestExecution, TestExecution.test_case_id == TestCase.id)
            .where(TestExecution.test_run_id == run_id)
        ).all()
    }
    assert refs == {"TC-E-001"}
