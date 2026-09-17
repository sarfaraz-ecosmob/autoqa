"""Authenticated-target support: credentials blob, auth flow, API auth headers.

Pure-function tests (no network). Browser login runs only in the
browser-worker image and is skipped elsewhere.
"""
import pytest

from app.execution.api_exec import ApiExecutor
from app.security.crypto import encrypt_json, mask_secrets
from app.testdata import decrypt_credentials


# ---------- credentials blob roundtrip ----------

def test_credentials_blob_roundtrip():
    blob = encrypt_json({"values": {"username": "qa@x.com", "password": "s3cret"}, "auth": {"login_url": "/login"}})
    values, auth = decrypt_credentials(blob)
    assert values["username"] == "qa@x.com"
    assert values["password"] == "s3cret"
    assert auth["login_url"] == "/login"


def test_credentials_blob_legacy_flat_format():
    blob = encrypt_json({"username": "old", "password": "style"})
    values, auth = decrypt_credentials(blob)
    assert values == {"username": "old", "password": "style"}
    assert auth == {}


def test_credentials_blob_garbage_returns_empty():
    values, auth = decrypt_credentials("not-a-blob")
    assert values == {} and auth == {}
    values2, auth2 = decrypt_credentials("")
    assert values2 == {} and auth2 == {}


def test_masked_serialization_never_reveals_password():
    blob = encrypt_json({"values": {"username": "qa@x.com", "password": "s3cret-long"}, "auth": {"api_auth_header_value": "tok-abc123"}})
    values, auth = decrypt_credentials(blob)
    masked = mask_secrets(values)
    assert masked["password"] == "••••••••"
    assert masked["username"] == "qa@x.com"
    assert "s3cret" not in str(mask_secrets(auth))


# ---------- auth flow normalization ----------

def test_normalize_auth_merges_defaults():
    from app.execution.auth_flow import normalize_auth

    merged = normalize_auth({"login_url": "https://x/login"})
    assert merged["login_url"] == "https://x/login"
    assert merged["username_selector"]  # default present
    assert merged["has_credentials"] is True

    merged2 = normalize_auth({"api_auth_header": ""})  # explicit disable
    assert merged2["api_auth_header"] == ""


# ---------- API auth header resolution (mocked transport) ----------

class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def test_resolve_api_auth_headers_static_token(monkeypatch):
    from app.execution import auth_flow

    monkeypatch.setattr(auth_flow, "fetch_api_token", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch")))

    headers = auth_flow.resolve_api_auth_headers(
        {"api_auth_header": "Authorization", "api_auth_prefix": "Bearer ", "api_auth_header_value": "static-tok"},
        {"username": "u", "password": "p"},
        {},
        "http://demo-app:9000",
        [],
    )
    assert headers == {"Authorization": "Bearer static-tok"}


def test_resolve_api_auth_headers_from_token_endpoint(monkeypatch):
    from app.execution import auth_flow

    captured = {}

    def fake_fetch(auth, credentials, variables, base_url):
        captured["path"] = auth["api_token_request"]["path"]
        return "tok-123"

    monkeypatch.setattr(auth_flow, "fetch_api_token", fake_fetch)
    headers = auth_flow.resolve_api_auth_headers(
        {"api_auth_header": "Authorization", "api_auth_prefix": "Bearer ",
         "api_token_request": {"path": "/api/auth/login", "token_path": "token"}},
        {"username": "u", "password": "p"},
        {},
        "http://demo-app:9000",
        [],
    )
    assert headers == {"Authorization": "Bearer tok-123"}
    assert captured["path"] == "/api/auth/login"


def test_resolve_api_auth_headers_disabled():
    from app.execution import auth_flow

    headers = auth_flow.resolve_api_auth_headers(
        {"api_auth_header": "", "api_auth_header_value": "x"}, {}, {}, "http://demo-app:9000", []
    )
    assert headers == {}


def test_resolve_api_auth_headers_placeholder_in_static_value():
    from app.execution import auth_flow

    headers = auth_flow.resolve_api_auth_headers(
        {"api_auth_header": "X-Key", "api_auth_prefix": "", "api_auth_header_value": "{{api_key}}"},
        {},
        {"api_key": "abc123"},
        "http://demo-app:9000",
        [],
    )
    assert headers == {"X-Key": "abc123"}


# ---------- fetch_api_token request + extraction ----------

def test_fetch_api_token_extracts_from_response(monkeypatch):
    from app.execution import auth_flow

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, path, **kwargs):
            assert method == "POST"
            assert path == "/api/auth/login"
            assert kwargs["json"] == {"username": "u", "password": "p"}
            return _FakeResp({"data": {"token": "tok-xyz"}})

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    token = auth_flow.fetch_api_token(
        {"api_token_request": {"path": "/api/auth/login", "json": {"username": "{{username}}", "password": "{{password}}"}, "token_path": "data.token"}},
        {"username": "u", "password": "p"},
        {},
        "http://demo-app:9000",
    )
    assert token == "tok-xyz"


def test_fetch_api_token_noop_without_request():
    from app.execution import auth_flow

    assert auth_flow.fetch_api_token({}, {"username": "u", "password": "p"}, {}, "http://demo-app:9000") == ""


def test_fetch_api_token_missing_path_returns_empty(monkeypatch):
    from app.execution import auth_flow

    token = auth_flow.fetch_api_token(
        {"api_token_request": {"path": "/nope", "token_path": "token"}},
        {"username": "u", "password": "p"},
        {},
        "http://demo-app:9000",
    )
    assert token == ""


# ---------- success assertions (pure logic via fake page) ----------

class _FakePage:
    def __init__(self, url, texts=(), selectors=()):
        self.url = url
        self._texts = set(texts)
        self._selectors = set(selectors)

    def get_by_text(self, t):
        class _L:
            def __init__(self, n):
                self.n = n

            def count(self):
                return 1 if self.n in self.outer._texts else 0

        l = _L(t)
        l.outer = self
        return l

    def locator(self, s):
        class _L:
            def __init__(self, n):
                self.n = n

            def count(self):
                return 1 if self.n in self.outer._selectors else 0

        l = _L(s)
        l.outer = self
        return l


def test_assert_pass_kinds():
    from app.execution.auth_flow import _assert_pass

    page = _FakePage("https://x/dashboard", texts=("Welcome",), selectors=("#logout",))
    assert _assert_pass("url_not_contains:login", page, None) is True
    assert _assert_pass("url_contains:dashboard", page, None) is True
    assert _assert_pass("text_present:Welcome", page, None) is True
    assert _assert_pass("selector_present:#logout", page, None) is True
    assert _assert_pass("url_contains:login", page, None) is False
    assert _assert_pass("api_status:200", page, 200) is True
    assert _assert_pass("api_status:200", page, 500) is False
    assert _assert_pass("unknown_kind:x", page, None) is False


def test_assert_pass_empty_and_none_values():
    from app.execution.auth_flow import _assert_pass

    page = _FakePage("https://x/login")
    # Degenerate assertions (empty value) fail-safe → False, so a misconfigured
    # success check surfaces in the auth log instead of silently passing.
    assert _assert_pass("url_not_contains:", page, None) is False
    assert _assert_pass("url_contains:", page, None) is False
    assert _assert_pass("text_present:", page, None) is False
    assert _assert_pass("selector_present:", page, None) is False
    assert _assert_pass("", page, None) is False


# ---------- ApiExecutor auth wiring ----------

def test_api_executor_accepts_auth_and_credentials():
    ex = ApiExecutor("http://demo-app:9000", auth={"api_auth_header": "Authorization"}, credentials={"username": "u"})
    assert ex.auth["api_auth_header"] == "Authorization"
    assert ex.credentials["username"] == "u"
    assert ex.auth_headers == {}


def test_api_executor_auth_guard_warns_without_headers():
    ex = ApiExecutor("http://demo-app:9000", auth={"api_auth_header": "Authorization"}, credentials={"username": "u"})
    guard = ex._unauthenticated_guard([])
    assert guard and guard[0]["result"] == "warn"


def test_api_executor_no_guard_without_credentials():
    ex = ApiExecutor("http://demo-app:9000")
    assert ex._unauthenticated_guard([]) is None


# ---------- generation: placeholders + payment scenarios ----------

def test_generation_uses_placeholders_not_secrets():
    from app.generation.testcases import generate_cases

    pages = [{"url": "http://x/login", "title": "Login", "depth": 1, "components": {}}]
    cases = generate_cases("http://x", pages, [], has_credentials=True)
    login_case = next(c for c in cases if c["module"] == "login" and c["positive"])
    assert login_case["test_data"]["username"] == "{{username}}"
    assert login_case["test_data"]["password"] == "{{password}}"
    blob = str(cases)
    assert "DemoPass123!" not in blob


def test_generation_payment_scenarios_when_checkout_seen():
    from app.generation.testcases import generate_cases

    apis = [{"method": "POST", "path": "/api/checkout", "group": "orders"}]
    cases = generate_cases("http://x", [], apis, has_credentials=True)
    payment = [c for c in cases if c["module"] == "payment"]
    assert len(payment) >= 3  # success + declined + idempotency
    assert any("{{card_number}}" in str(c["test_data"]) for c in payment)
    assert any(c["positive"] is False for c in payment)
    # decline case uses the standard decline test card
    decline = next(c for c in payment if "declined" in c["scenario"].lower())
    assert "4000000000000002" in str(decline["steps"])


def test_negative_payment_case_expects_handled_4xx():
    """§6-style graceful-handling rule: negative cases assert no server error."""
    from app.generation.testcases import generate_cases

    apis = [{"method": "POST", "path": "/api/checkout", "group": "orders"}]
    cases = generate_cases("http://x", [], apis, has_credentials=False)
    decline = next(c for c in cases if c["module"] == "payment" and not c["positive"] and "declined" in c["scenario"].lower())
    assert "never HTTP 5xx" in decline["expected_result"]


def test_generation_no_payment_scenarios_without_evidence():
    from app.generation.testcases import generate_cases

    cases = generate_cases("http://x", [], [{"method": "GET", "path": "/api/users", "group": "users"}], has_credentials=False)
    assert not [c for c in cases if c["module"] == "payment"]


# ---------- resolve_variables still yields credential values ----------

def test_resolve_variables_includes_credentials():
    """Credential values land in the executor variables (for {{placeholders}})."""
    from app.models import Project
    from app.testdata import resolve_variables

    blob = encrypt_json({"values": {"username": "qa@x.com", "password": "pw"}, "auth": {}})
    proj = Project(owner_id="00000000-0000-0000-0000-000000000000", name="t", base_url="http://x", credentials_encrypted=blob, settings={})

    class FakeDB:
        """env lookup → None, env count → 0, datasets → none."""

        def execute(self, *a, **k):
            class R:
                def scalar_one_or_none(self):
                    return None

                def scalar_one(self):
                    return 0

                def scalars(self):
                    class S:
                        def all(self):
                            return []

                    return S()

            return R()

    vars_ = resolve_variables(proj, environment_id="", db=FakeDB())
    assert vars_["username"] == "qa@x.com"
    assert vars_["password"] == "pw"
