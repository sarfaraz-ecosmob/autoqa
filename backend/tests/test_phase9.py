"""Phase 9 unit tests: API executor chaining, extraction, env vars."""
import pytest

from app.execution.api_exec import ApiExecutor, extract_path, substitute_vars


def test_substitute_vars_strings_and_nested():
    assert substitute_vars("/api/users/{{user_id}}", {"user_id": 7}) == "/api/users/7"
    assert substitute_vars({"a": "{{x}}", "b": ["{{x}}", 1]}, {"x": "v"}) == {"a": "v", "b": ["v", 1]}
    assert substitute_vars("no vars", {}) == "no vars"


def test_extract_path():
    body = {"data": {"users": [{"id": 1}, {"id": 2}]}, "meta": {"total": 2}}
    assert extract_path(body, "meta.total") == 2
    assert extract_path(body, "data.users[1].id") == 2
    assert extract_path(body, "missing.path") is None


def test_chained_flow_with_extract_and_reuse():
    """extract → substitute chain using a local echo of the target app."""
    ex = ApiExecutor("http://demo-app:9000")
    result = ex.run([
        {"action": "request", "method": "GET", "path": "/api/orders/1"},
        {"action": "extract", "path": "status", "var": "order_status", "required": False},
        {"action": "request", "method": "GET", "path": "/api/orders/{{order_status}}"},
        {"action": "expect_status", "value": "4xx"},  # status text isn't an int id → 422/404
    ])
    # demo-app reachable inside compose network; tolerate sandbox without it
    if result["error_details"].get("type") == "exception":
        pytest.skip("demo-app not reachable from this image")
    assert result["status"] in ("passed", "failed")
    assert any(s["action"] == "extract" for s in result["log"])


def test_expect_json_value_pass_and_fail():
    ex = ApiExecutor("http://demo-app:9000")
    result = ex.run([
        {"action": "request", "method": "GET", "path": "/api/users"},
        {"action": "expect_json_value", "path": "users[0].name", "value": "alice"},
    ])
    if result["error_details"].get("type") == "exception":
        pytest.skip("demo-app not reachable from this image")
    assert result["status"] == "passed"

    result2 = ex.run([
        {"action": "request", "method": "GET", "path": "/api/users"},
        {"action": "expect_json_value", "path": "users[0].name", "value": "nobody"},
    ])
    assert result2["status"] == "failed"
    assert result2["error_details"]["type"] == "value_mismatch"


def test_latency_captured():
    ex = ApiExecutor("http://demo-app:9000")
    result = ex.run([
        {"action": "request", "method": "GET", "path": "/health"},
        {"action": "expect_response_time_under_ms", "value": 5000},
    ])
    if result["error_details"].get("type") == "exception":
        pytest.skip("demo-app not reachable")
    assert isinstance(result.get("latency_ms"), int)
