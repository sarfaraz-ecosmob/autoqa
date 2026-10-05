"""Credentials & auth-config API behavior (masking, reveal, re-save safety)
plus the async login twin used by the discovery crawler.

Uses the repo's isolation pattern (minimal FastAPI app with just the projects
router + SQLite via dependency overrides). Pure-function and fake-page tests
cover the async login path without a real browser.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# ---------- fixture ----------

@pytest.fixture()
def client_and_project():
    """Isolated app + one owned project; returns (client, project_id)."""
    from fastapi import FastAPI

    from app.api import projects as projects_router
    from app.api.deps import get_current_user, get_db
    from app.models import Base, Project, Role, User

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    user = User(email="authcfg-test@example.com", password_hash="x", role=Role.admin)
    db.add(user)
    db.commit()
    project = Project(owner_id=user.id, name="AuthCfg", base_url="http://demo-app:9000", settings={})
    db.add(project)
    db.commit()
    pid = project.id

    app = FastAPI()
    app.include_router(projects_router.router)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: user

    yield TestClient(app), pid

    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def _save_auth(client, project_id, values=None, auth=None):
    body = {}
    if values is not None:
        body["credentials"] = values
    if auth is not None:
        body["auth"] = auth
    return client.patch(f"/api/projects/{project_id}", json=body)


# ---------- auth config masking: selectors/URLs survive the round-trip ----------

def test_get_project_shows_auth_config_not_masked(client_and_project):
    client, pid = client_and_project
    _save_auth(
        client, pid,
        values={"username": "qa@x.com", "password": "s3cret-long"},
        auth={
            "login_url": "http://demo-app:9000/login",
            "username_selector": "input[name='username']",
            "password_selector": "input[type='password']",
            "submit_selector": "button[type='submit']",
            "success_assert": "url_not_contains:login",
        },
    )
    data = client.get(f"/api/projects/{pid}").json()
    # The exact regression: password_selector used to come back as ••••••••
    assert data["auth"]["password_selector"] == "input[type='password']"
    assert data["auth"]["login_url"] == "http://demo-app:9000/login"
    assert data["auth"]["success_assert"] == "url_not_contains:login"
    # but credential VALUES stay masked
    assert data["credentials"]["password"] == "••••••••"
    assert data["credentials"]["username"] == "qa@x.com"


def test_resave_with_defaults_does_not_clobber_saved_config(client_and_project):
    """The corruption path: UI saved masked placeholders back → real values lost."""
    client, pid = client_and_project
    _save_auth(
        client, pid,
        values={"username": "u", "password": "p"},
        auth={"login_url": "http://demo-app:9000/login", "username_selector": "#my-user"},
    )
    # Round-trip like the old UI: re-save exactly what GET returned
    got = client.get(f"/api/projects/{pid}").json()
    r = _save_auth(client, pid, values={}, auth=got["auth"])
    assert r.status_code == 200
    data = client.get(f"/api/projects/{pid}").json()
    assert data["auth"]["username_selector"] == "#my-user"
    assert data["auth"]["login_url"] == "http://demo-app:9000/login"


def test_mask_auth_masks_only_tokenish_values():
    from app.api.projects import _mask_auth

    safe = _mask_auth(
        {
            "login_url": "http://x/login",
            "password_selector": "input[type='password']",
            "api_auth_header_value": "real-token-123",
            "success_assert": "url_not_contains:login",
        }
    )
    assert safe["login_url"] == "http://x/login"
    assert safe["password_selector"] == "input[type='password']"
    assert safe["api_auth_header_value"] == "••••••••"
    assert safe["success_assert"] == "url_not_contains:login"


# ---------- credentials endpoint: masked by default, reveal decrypts ----------

def test_credentials_endpoint_masks_and_reveals(client_and_project):
    client, pid = client_and_project
    _save_auth(
        client, pid,
        values={"username": "qa@x.com", "password": "s3cret-long"},
        auth={"login_url": "http://demo-app:9000/login"},
    )

    masked = client.get(f"/api/projects/{pid}/credentials").json()
    assert masked["has_credentials"] is True
    assert masked["credentials"]["password"] == "••••••••"
    assert masked["credentials"]["username"] == "qa@x.com"

    revealed = client.get(f"/api/projects/{pid}/credentials?reveal=true").json()
    assert revealed["credentials"]["password"] == "s3cret-long"
    assert revealed["auth"]["login_url"] == "http://demo-app:9000/login"


def test_credentials_endpoint_requires_auth(client_and_project):
    client, pid = client_and_project
    app_with_auth = client.app
    # Simulate a fresh visitor: drop the current-user override
    from app.api.deps import get_current_user

    saved = app_with_auth.dependency_overrides.pop(get_current_user)
    try:
        r = client.get(f"/api/projects/{pid}/credentials")
        assert r.status_code in (401, 403)
    finally:
        app_with_auth.dependency_overrides[get_current_user] = saved


# ---------- test-sign-in endpoint validation ----------

def test_test_sign_in_requires_saved_login_url(client_and_project):
    client, pid = client_and_project
    r = client.post(f"/api/projects/{pid}/test-sign-in")
    assert r.status_code == 422
    assert "login" in r.json()["detail"].lower()


def test_test_sign_in_dispatches_when_configured(client_and_project, monkeypatch):
    client, pid = client_and_project
    _save_auth(
        client, pid,
        values={"username": "u", "password": "p"},
        auth={"login_url": "http://demo-app:9000/login"},
    )
    sent = {}

    class FakeCelery:
        def send_task(self, name, kwargs=None, task_id=None):
            sent["name"] = name
            sent["kwargs"] = kwargs
            sent["task_id"] = task_id

    import app.api.projects as projects_mod

    monkeypatch.setattr(projects_mod, "celery_app", FakeCelery())
    r = client.post(f"/api/projects/{pid}/test-sign-in")
    assert r.status_code == 202
    assert sent["name"] == "app.test_project_login"
    assert sent["kwargs"]["project_id"] == pid


# ---------- async login twin (fake async page — the crawler path) ----------

class _FakeAsyncLocator:
    def __init__(self, n):
        self._n = n

    async def count(self):
        return self._n


class _FakeAsyncPage:
    """Minimal async Playwright page: records fills/clicks, tracks URL."""

    def __init__(self, selectors_present, landing_url="http://x/dashboard"):
        self._selectors = set(selectors_present)
        self.url = "http://x/login"
        self._landing = landing_url
        self.filled = []
        self.clicked = []

    async def goto(self, url, timeout=None, wait_until=None):
        self.url = url
        return type("R", (), {"status": 200})()

    async def fill(self, selector, value, timeout=None):
        self.filled.append((selector, value))

    async def click(self, selector, timeout=None):
        self.clicked.append(selector)
        self.url = self._landing  # simulate redirect after submit

    async def wait_for_timeout(self, ms):
        return None

    def get_by_text(self, text):
        return _FakeAsyncLocator(1 if text == "Welcome" else 0)

    def locator(self, sel):
        return _FakeAsyncLocator(1 if sel in self._selectors else 0)

    async def title(self):
        return "Dashboard"


async def test_browser_login_async_fills_and_passes_url_assert():
    from app.execution.auth_flow import browser_login_async

    page = _FakeAsyncPage(
        selectors_present={"input[name='username']", "input[type='password']", "button[type='submit']"}
    )
    log = []
    ok = await browser_login_async(
        page,
        {"login_url": "http://x/login", "success_assert": "url_not_contains:login"},
        {"username": "u", "password": "p"},
        {},
        log,
    )
    assert ok is True
    assert page.filled == [("input[name='username']", "u"), ("input[type='password']", "p")]
    assert page.clicked == ["button[type='submit']"]
    assert log[-1]["result"] == "pass"


async def test_browser_login_async_fails_when_assertion_fails():
    from app.execution.auth_flow import browser_login_async

    page = _FakeAsyncPage(selectors_present=set(), landing_url="http://x/login?error=1")
    ok = await browser_login_async(
        page,
        {"login_url": "http://x/login", "success_assert": "url_not_contains:login"},
        {"username": "u", "password": "WRONG"},
        {},
        [],
    )
    assert ok is False


async def test_browser_login_async_no_login_url_is_false():
    from app.execution.auth_flow import browser_login_async

    page = _FakeAsyncPage(selectors_present=set())
    assert await browser_login_async(page, {}, {"username": "u"}, {}, []) is False


async def test_browser_login_async_heals_missing_selector():
    """username selector missing → fallback candidate (derived id) resolves it."""
    from app.execution.auth_flow import browser_login_async

    page = _FakeAsyncPage(
        selectors_present={"#username", "input[type='password']", "button[type='submit']"}
    )
    log = []
    ok = await browser_login_async(
        page,
        {
            "login_url": "http://x/login",
            "username_selector": 'input[name="username"]',
            "success_assert": "url_not_contains:login",
        },
        {"username": "u", "password": "p"},
        {},
        log,
    )
    assert ok is True
    healed = [e for e in log if e.get("action") == "auth_heal"]
    assert healed and healed[0]["healed_to"] == "#username"
    assert page.filled[0][0] == "#username"


# ---------- worker task wiring ----------

def test_test_project_login_task_on_browser_queue():
    from app.tasks import celery_app

    assert celery_app.conf.task_routes["app.test_project_login"]["queue"] == "browser"
    import app.worker_tasks as wt

    assert wt.test_project_login.name == "app.test_project_login"


def test_detect_and_dry_run_tasks_on_browser_queue():
    from app.tasks import celery_app
    import app.worker_tasks as wt

    assert celery_app.conf.task_routes["app.detect_login_selectors"]["queue"] == "browser"
    assert celery_app.conf.task_routes["app.dry_run_login"]["queue"] == "browser"
    assert wt.detect_login_selectors.name == "app.detect_login_selectors"
    assert wt.dry_run_login.name == "app.dry_run_login"


# ---------- detect-login endpoint ----------

def test_detect_login_dispatches(client_and_project, monkeypatch):
    client, pid = client_and_project
    sent = {}

    class FakeCelery:
        def send_task(self, name, kwargs=None, task_id=None):
            sent.update(name=name, kwargs=kwargs, task_id=task_id)

    import app.api.projects as projects_mod

    monkeypatch.setattr(projects_mod, "celery_app", FakeCelery())
    r = client.post(f"/api/projects/{pid}/detect-login", json={"login_url": "http://demo-app:9000/login"})
    assert r.status_code == 202
    assert sent["name"] == "app.detect_login_selectors"
    assert sent["kwargs"]["login_url"].endswith("/login")


def test_detect_login_rejects_ssrf_url(client_and_project):
    """The worker task itself rejects private/loopback targets not allowlisted."""
    from app.worker_tasks import detect_login_selectors

    out = detect_login_selectors.run(project_id="x", login_url="http://169.254.169.254/latest/meta-data/")
    assert out["status"] == "failed"
    assert "rejected" in out["error"]


# ---------- dry-run endpoint: overrides reach the task, nothing saved ----------

def test_dry_run_dispatches_form_overrides(client_and_project, monkeypatch):
    client, pid = client_and_project
    sent = {}

    class FakeCelery:
        def send_task(self, name, kwargs=None, task_id=None):
            sent.update(name=name, kwargs=kwargs, task_id=task_id)

    import app.api.projects as projects_mod

    monkeypatch.setattr(projects_mod, "celery_app", FakeCelery())
    r = client.post(
        f"/api/projects/{pid}/dry-run-login",
        json={
            "login_url": "http://demo-app:9000/login",
            "username": "form-user",
            "password": "form-pass",
            "username_selector": "#u",
        },
    )
    assert r.status_code == 202
    assert sent["kwargs"]["credentials_override"] == {"username": "form-user", "password": "form-pass"}
    assert sent["kwargs"]["auth_override"]["username_selector"] == "#u"

    # And nothing was persisted
    data = client.get(f"/api/projects/{pid}").json()
    assert data["has_credentials"] is False


def test_dry_run_task_requires_username_and_password():
    """No overrides + no saved credentials → structured failure, no crash."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base, Project, Role, User
    from app.worker_tasks import dry_run_login

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    user = User(email="dr@example.com", password_hash="x", role=Role.admin)
    db.add(user)
    db.commit()
    project = Project(owner_id=user.id, name="dr", base_url="http://demo-app:9000", settings={})
    db.add(project)
    db.commit()
    pid = project.id
    db.close()

    try:
        import app.worker_tasks as wt

        orig = wt.SessionLocal
        wt.SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        try:
            out = dry_run_login.run(
                project_id=pid,
                auth_override={"login_url": "http://demo-app:9000/login"},
                credentials_override={},
            )
            assert out["status"] == "failed"
            assert "username and password" in out["error"]
        finally:
            wt.SessionLocal = orig
    finally:
        engine.dispose()


# ---------- test-api-auth endpoint (sync; httpx + resolution mocked where needed) ----------

def test_api_auth_noop_without_token_source(client_and_project):
    client, pid = client_and_project
    r = client.post(
        f"/api/projects/{pid}/test-api-auth",
        json={"api_auth_header": "Authorization", "api_auth_prefix": "Bearer "},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "No API auth" in body["message"]


def test_api_auth_success_sends_header(monkeypatch):
    """Static token + reachable target → ok with masked header echo."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.api import projects as projects_router
    from app.api.deps import get_current_user, get_db
    from app.models import Base, Project, Role, User

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    user = User(email="api@example.com", password_hash="x", role=Role.admin)
    db.add(user)
    db.commit()
    project = Project(owner_id=user.id, name="api", base_url="http://demo-app:9000", settings={})
    db.add(project)
    db.commit()
    pid = project.id

    app = FastAPI()
    app.include_router(projects_router.router)

    def _yield_db():
        yield db

    app.dependency_overrides[get_db] = _yield_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)

    # demo-app:9000 is allowlisted but unreachable in unit tests → fake httpx.get
    class FakeResp:
        status_code = 200

    import httpx as _httpx

    captured = {}

    def fake_get(url, headers=None, timeout=None, follow_redirects=None):
        captured.update(url=url, headers=headers)
        return FakeResp()

    monkeypatch.setattr(_httpx, "get", fake_get)

    r = client.post(
        f"/api/projects/{pid}/test-api-auth",
        json={"api_auth_header": "Authorization", "api_auth_prefix": "Bearer ", "api_auth_header_value": "tok-123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "Dry run successful" in body["message"]
    assert body["header"]["name"] == "Authorization"
    assert "tok-123" not in body["header"]["value_masked"]  # masked!
    assert captured["headers"] == {"Authorization": "Bearer tok-123"}
    db.close()
    engine.dispose()
