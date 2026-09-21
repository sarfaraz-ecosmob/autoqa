"""Project deletion regression tests (FK cascade).

`DELETE /api/projects/{id}` used to fail with a ForeignKeyViolation as soon as
any child row outside the three ORM-cascaded collections (pages/apis/
requirements) existed — e.g. an `applications` row created by the first
analysis run. These tests seed EVERY project-scoped child table and delete the
project with real FK enforcement (SQLite `PRAGMA foreign_keys=ON`), so any
missing/miss-ordered cascade step fails loudly here instead of in production.
"""
import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    AccessibilityResult,
    ApiEndpoint,
    Application,
    Base,
    Component,
    Defect,
    Environment,
    Page,
    PerformanceResult,
    Project,
    Report,
    Requirement,
    RequirementLink,
    Role,
    SecurityFinding,
    TestCase,
    TestDataset,
    TestEnvironment,
    TestExecution,
    TestPlan,
    TestRun,
    TestSchedule,
    TestStatus,
    TestSuite,
    User,
    VisualBaseline,
    VisualCheck,
    Webhook,
)


# Every table that references projects.id — keeps this test honest if a new
# project-scoped model is added but forgotten in the delete cascade.
_PROJECT_SCOPED_MODELS = (
    TestEnvironment,
    TestDataset,
    Environment,
    Application,
    Page,
    ApiEndpoint,
    Component,
    TestPlan,
    TestCase,
    TestSuite,
    TestRun,
    TestExecution,
    Defect,
    SecurityFinding,
    PerformanceResult,
    AccessibilityResult,
    Report,
    TestSchedule,
    Webhook,
    VisualBaseline,
    VisualCheck,
    Requirement,
    RequirementLink,
)


def _make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _record):  # real referential enforcement
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_full_project(db) -> tuple[str, str]:
    """One project with a row in every project-scoped child table."""
    user = User(email="delete-owner@example.com", password_hash="x", role=Role.admin)
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        name="Cascade Victim",
        base_url="http://demo-app:9000",
        authorization_confirmed=True,
        settings={},
    )
    db.add(project)
    db.flush()
    pid = project.id

    page = Page(project_id=pid, url="http://demo-app:9000/login", title="Login", depth=0)
    api = ApiEndpoint(project_id=pid, method="GET", path="/api/items", group="items")
    plan = TestPlan(project_id=pid, objective="objective", sections=[])
    env = TestEnvironment(project_id=pid, name="staging", base_url="http://demo-app:9000")
    suite = TestSuite(project_id=pid, name="regression", test_case_ids=[])
    requirement = Requirement(project_id=pid, external_id="REQ-DEL-001", title="Login works")
    db.add_all([page, api, plan, env, suite, requirement])
    db.flush()

    db.add_all([
        Application(project_id=pid, frontend_stack={}, backend_stack={}, architecture_map={}),
        Environment(project_id=pid, name="dev", variables_encrypted="{}"),
        Component(project_id=pid, page_id=page.id, kind="form", name="login-form"),
        TestDataset(project_id=pid, name="creds", environment_id=env.id),
        TestCase(project_id=pid, test_plan_id=plan.id, ref="TC-DEL-001", scenario="login", steps=[]),
        TestSchedule(project_id=pid, name="nightly", cron="0 2 * * *", environment_id=env.id),
        Webhook(project_id=pid, name="slack", url_encrypted="", events=[]),
        SecurityFinding(project_id=pid, title="TLS", severity="high"),
        PerformanceResult(project_id=pid, scenario="login load"),
        AccessibilityResult(project_id=pid, page_url="http://demo-app:9000/login"),
        Report(project_id=pid, format="csv"),
    ])
    db.flush()

    # Rows whose FKs point at other children — seeded to prove ordering.
    run = TestRun(project_id=pid, suite_id=suite.id, label="run-1", browsers=["chromium"])
    db.add(run)
    db.flush()
    case = db.execute(
        sa.select(TestCase).where(TestCase.project_id == pid)
    ).scalar_one()
    execution = TestExecution(
        test_run_id=run.id, test_case_id=case.id, status=TestStatus.failed, attempt=1,
    )
    db.add(execution)
    db.flush()
    db.add_all([
        Defect(project_id=pid, execution_id=execution.id, title="login broke"),
        VisualCheck(project_id=pid, test_case_id=case.id, execution_id=execution.id),
        VisualBaseline(project_id=pid, test_case_id=case.id, browser="chromium",
                       storage_key="baselines/del-001.png"),
        RequirementLink(project_id=pid, requirement_id=requirement.id, test_case_id=case.id),
    ])
    db.commit()
    return pid, user.id


def _assert_project_gone(db, project_id: str) -> None:
    assert db.execute(
        sa.select(sa.func.count()).select_from(Project).where(Project.id == project_id)
    ).scalar_one() == 0
    for model in _PROJECT_SCOPED_MODELS:
        if model is TestExecution:
            # test_executions has no project_id column; check via runs.
            left = db.execute(
                sa.select(sa.func.count()).select_from(TestExecution).where(
                    TestExecution.test_run_id.in_(
                        sa.select(TestRun.id).where(TestRun.project_id == project_id)
                    )
                )
            ).scalar_one()
        else:
            left = db.execute(
                sa.select(sa.func.count()).select_from(model).where(model.project_id == project_id)
            ).scalar_one()
        assert left == 0, f"{model.__tablename__}: {left} orphaned row(s) left after delete"


def test_cascade_delete_removes_every_child_row():
    db = _make_db()
    try:
        pid, _ = _seed_full_project(db)

        from app.api.projects import _delete_project_cascade

        _delete_project_cascade(db, pid)
        db.commit()
        _assert_project_gone(db, pid)
    finally:
        db.close()


def test_old_delete_style_fails_with_fk_enforcement():
    """Guards the regression: naive project-only delete must raise on FKs."""
    db = _make_db()
    try:
        pid, _ = _seed_full_project(db)

        db.execute(sa.delete(Application).where(Application.project_id == pid))
        db.commit()
        # Even with applications gone, other children still block a plain delete.
        with pytest.raises(IntegrityError):
            db.execute(sa.delete(Project).where(Project.id == pid))
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_delete_endpoint_returns_204_with_full_data():
    """API-level: the route cascades and answers 204, not 500."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import projects as projects_router
    from app.api.deps import get_current_user, get_db

    db = _make_db()
    try:
        pid, user_id = _seed_full_project(db)

        app = FastAPI()
        app.include_router(projects_router.router)

        def _override_db():
            yield db

        user = db.execute(sa.select(User).where(User.id == user_id)).scalar_one()
        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: user

        client = TestClient(app)
        resp = client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 204, resp.text
        _assert_project_gone(db, pid)
    finally:
        db.close()
