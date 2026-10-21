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

    async def press(self, selector, key):
        self.clicked.append(f"press:{selector}:{key}")
        self.url = self._landing

    async def wait_for_load_state(self, state=None, timeout=None):
        return None

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


# ---------- two-factor authentication (OTP / 2FA) ----------

_RFC6238_SECRET_B32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # ASCII "12345678901234567890"


def test_totp_matches_rfc6238_vectors():
    from app.execution.auth_flow import totp

    # RFC 6238 Appendix B (SHA-1): t=59 → 94287082, t=1111111109 → 07081804
    assert totp(_RFC6238_SECRET_B32, digits=8, at=59) == "94287082"
    assert totp(_RFC6238_SECRET_B32, digits=6, at=59) == "287082"
    assert totp(_RFC6238_SECRET_B32, digits=6, at=1111111109) == "081804"
    # padding optional, case-insensitive, always zero-padded
    assert totp(_RFC6238_SECRET_B32.lower().rstrip("="), digits=6, at=59) == "287082"


def test_generate_otp_prefers_static_then_totp():
    from app.execution.auth_flow import generate_otp, totp

    assert generate_otp({}, {}) == ""
    assert generate_otp({"otp_secret": _RFC6238_SECRET_B32}, {}) == totp(_RFC6238_SECRET_B32)
    # a static/backdoor code wins over the TOTP secret
    assert generate_otp({"otp_secret": _RFC6238_SECRET_B32, "otp_code": "135790"}, {}) == "135790"
    # invalid base32 → empty (OTP step skipped), never an exception
    assert generate_otp({"otp_secret": "not base32!!"}, {}) == ""
    # {{placeholders}} resolve from variables
    assert generate_otp({"otp_code": "{{code}}"}, {"code": "999111"}) == "999111"


async def _otp_async_login(credentials, selectors_present, auth_extra=None):
    """Run browser_login_async with the fake page + given OTP setup."""
    from app.execution.auth_flow import browser_login_async

    page = _FakeAsyncPage(selectors_present=selectors_present)
    log = []
    auth = {
        "login_url": "http://x/login",
        "success_assert": "url_not_contains:login",
        **(auth_extra or {}),
    }
    ok = await browser_login_async(page, auth, credentials, {}, log)
    return ok, page, log


async def test_browser_login_async_fills_otp_step():
    """2FA configured → fresh TOTP filled into the OTP input + submitted."""
    from app.execution.auth_flow import totp

    ok, page, log = await _otp_async_login(
        {"username": "u", "password": "p", "otp_secret": _RFC6238_SECRET_B32},
        selectors_present={
            "input[name='username']",
            "input[type='password']",
            "button[type='submit']",
            "input[name='otp']",
            "button.otp-continue",
        },
        auth_extra={"otp_selector": "input[name='otp']", "otp_submit_selector": "button.otp-continue"},
    )
    assert ok is True
    assert ("input[name='otp']", totp(_RFC6238_SECRET_B32)) in page.filled
    assert "button[type='submit']" in page.clicked
    assert "button.otp-continue" in page.clicked
    otp_events = [e for e in log if e.get("action") == "auth_otp"]
    assert otp_events and otp_events[0]["result"] == "pass"
    # the code itself must never be logged
    assert totp(_RFC6238_SECRET_B32) not in str(log)


async def test_browser_login_async_auto_detects_otp_input():
    """No otp_selector configured → the one-time-code candidate is probed."""
    from app.execution.auth_flow import totp

    ok, page, log = await _otp_async_login(
        {"username": "u", "password": "p", "otp_secret": _RFC6238_SECRET_B32},
        selectors_present={
            "input[name='username']",
            "input[type='password']",
            "button[type='submit']",
            "input[autocomplete='one-time-code']",
        },
    )
    assert ok is True
    assert ("input[autocomplete='one-time-code']", totp(_RFC6238_SECRET_B32)) in page.filled


async def test_browser_login_async_skips_otp_when_not_prompted():
    """2FA configured but the target shows no OTP input → login proceeds."""
    ok, page, log = await _otp_async_login(
        {"username": "u", "password": "p", "otp_secret": _RFC6238_SECRET_B32},
        selectors_present={"input[name='username']", "input[type='password']", "button[type='submit']"},
    )
    assert ok is True
    otp_events = [e for e in log if e.get("action") == "auth_otp"]
    assert otp_events and otp_events[0]["result"] == "skipped"
    assert not any(sel == "input[autocomplete='one-time-code']" for sel, _ in page.filled)


async def test_browser_login_async_fails_when_otp_fill_fails():
    """OTP prompted but unfillable → the login fails loudly, not vacuously."""
    import asyncio

    from app.execution.auth_flow import browser_login_async

    class _BrokenOtpPage(_FakeAsyncPage):
        async def fill(self, selector, value, timeout=None):
            if selector == "input[name='otp']":
                raise RuntimeError("element is not editable")
            await super().fill(selector, value, timeout=timeout)

    page = _BrokenOtpPage(
        selectors_present={
            "input[name='username']",
            "input[type='password']",
            "button[type='submit']",
            "input[name='otp']",
        }
    )
    log = []
    ok = await browser_login_async(
        page,
        {"login_url": "http://x/login", "otp_selector": "input[name='otp']", "success_assert": "url_not_contains:login"},
        {"username": "u", "password": "p", "otp_secret": _RFC6238_SECRET_B32},
        {},
        log,
    )
    assert ok is False
    otp_events = [e for e in log if e.get("action") == "auth_otp"]
    assert otp_events and otp_events[0]["result"] == "fail"


class _FakeSyncLocator:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n


class _FakeSyncPage:
    """Sync twin of _FakeAsyncPage for the test-executor login path."""

    def __init__(self, selectors_present, landing_url="http://x/dashboard"):
        self._selectors = set(selectors_present)
        self.url = "http://x/login"
        self._landing = landing_url
        self.filled = []
        self.clicked = []

    def goto(self, url, timeout=None, wait_until=None):
        self.url = url

    def fill(self, selector, value, timeout=None):
        self.filled.append((selector, value))

    def click(self, selector, timeout=None):
        self.clicked.append(selector)
        self.url = self._landing

    def press(self, selector, key):
        self.clicked.append(f"press:{selector}:{key}")
        self.url = self._landing

    def locator(self, sel):
        return _FakeSyncLocator(1 if sel in self._selectors else 0)

    def get_by_text(self, text):
        return _FakeSyncLocator(1 if text == "Welcome" else 0)

    def wait_for_load_state(self, state=None, timeout=None):
        return None

    def wait_for_timeout(self, ms):
        return None

    def title(self):
        return "Dashboard"


def test_browser_login_sync_handles_otp_step():
    from app.execution.auth_flow import browser_login

    page = _FakeSyncPage(
        selectors_present={
            "input[name='username']",
            "input[type='password']",
            "button[type='submit']",
            "input[name='otp']",
        }
    )
    log = []
    ok = browser_login(
        page,
        {"login_url": "http://x/login", "otp_selector": "input[name='otp']", "success_assert": "url_not_contains:login"},
        {"username": "u", "password": "p", "otp_code": "445566"},
        {},
        log,
    )
    assert ok is True
    assert ("input[name='otp']", "445566") in page.filled
    assert any(e.get("action") == "auth_otp" and e.get("result") == "pass" for e in log)


def test_browser_login_sync_unchanged_without_otp():
    """No otp_secret/otp_code → zero OTP activity (no waits, no probes)."""
    from app.execution.auth_flow import browser_login

    page = _FakeSyncPage(
        selectors_present={"input[name='username']", "input[type='password']", "button[type='submit']"}
    )
    log = []
    ok = browser_login(
        page,
        {"login_url": "http://x/login", "success_assert": "url_not_contains:login"},
        {"username": "u", "password": "p"},
        {},
        log,
    )
    assert ok is True
    assert page.filled == [("input[name='username']", "u"), ("input[type='password']", "p")]
    assert not any(e.get("action") == "auth_otp" for e in log)


# ---------- OTP storage: encrypted, masked, re-save-safe ----------

def test_otp_values_masked_and_selectors_readable(client_and_project):
    client, pid = client_and_project
    _save_auth(
        client, pid,
        values={"username": "qa@x.com", "password": "s3cret-long", "otp_secret": _RFC6238_SECRET_B32, "otp_code": "135790"},
        auth={
            "login_url": "http://demo-app:9000/login",
            "otp_selector": "input[name='otp']",
            "otp_submit_selector": "button.otp-continue",
        },
    )
    data = client.get(f"/api/projects/{pid}").json()
    assert data["credentials"]["otp_secret"] == "••••••••"
    assert data["credentials"]["otp_code"] == "••••••••"
    assert data["auth"]["otp_selector"] == "input[name='otp']"
    assert data["auth"]["otp_submit_selector"] == "button.otp-continue"

    # re-saving the masked round-trip must NOT wipe the real OTP secret
    r = _save_auth(client, pid, values=data["credentials"], auth=data["auth"])
    assert r.status_code == 200
    revealed = client.get(f"/api/projects/{pid}/credentials?reveal=true").json()
    assert revealed["credentials"]["otp_secret"] == _RFC6238_SECRET_B32
    assert revealed["credentials"]["otp_code"] == "135790"


def test_dry_run_dispatches_otp_overrides(client_and_project, monkeypatch):
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
            "username": "u",
            "password": "p",
            "otp_secret": _RFC6238_SECRET_B32,
            "otp_selector": "input[name='otp']",
        },
    )
    assert r.status_code == 202
    assert sent["kwargs"]["credentials_override"]["otp_secret"] == _RFC6238_SECRET_B32
    assert sent["kwargs"]["auth_override"]["otp_selector"] == "input[name='otp']"
    # nothing persisted
    assert client.get(f"/api/projects/{pid}").json()["has_credentials"] is False


def test_api_token_request_resolves_otp_placeholder(monkeypatch):
    """{{otp}} in the token request body resolves to the current code."""
    from app.execution import auth_flow

    monkeypatch.setattr(auth_flow, "totp", lambda secret, digits=6, period=30, at=None: "123456")

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"token": "tok-abc"}

    class FakeClient:
        def __init__(self, base_url=None, timeout=None):
            captured["base_url"] = base_url

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, path, json=None, data=None, content=None, headers=None):
            captured.update(method=method, path=path, json=json)
            return FakeResp()

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "Client", FakeClient)

    token = auth_flow.fetch_api_token(
        {"api_token_request": {"path": "/api/auth/login", "json": {"username": "{{username}}", "otp": "{{otp}}"}, "token_path": "token"}},
        {"username": "qa@x.com", "password": "p", "otp_secret": _RFC6238_SECRET_B32},
        {},
        "http://demo-app:9000",
    )
    assert token == "tok-abc"
    assert captured["json"]["otp"] == "123456"
    assert captured["json"]["username"] == "qa@x.com"
