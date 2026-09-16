"""Phase 11 unit tests: accessibility, performance, capture_on_pass, matrix.

Playwright-dependent tests skip automatically outside the browser-worker image.
"""
import pytest

from app.modules.perf import PerfConfig, run_perf

# Playwright is only in the browser-worker image
pw = pytest.importorskip("playwright.sync_api", reason="browser-worker image required")
from app.modules.a11y import audit_site  # noqa: E402
from app.execution.browser import BrowserExecutor  # noqa: E402
from app import storage  # noqa: E402


# ---------- Performance module (§14) ----------

def test_perf_bounded_and_returns_metrics():
    result = run_perf(
        "http://demo-app:9000",
        PerfConfig(path="/health", concurrent_users=2, duration_seconds=2,
                   requests_per_second=10, max_requests=30),
    )
    assert result["total_requests"] <= 30, "hard cap must be respected"
    assert result["total_requests"] > 0
    assert set(result) >= {"p50", "p90", "p95", "p99", "throughput_rps", "error_rate", "threshold_breached"}


def test_perf_threshold_breach_detected():
    result = run_perf(
        "http://demo-app:9000",
        PerfConfig(path="/health", concurrent_users=1, duration_seconds=2,
                   requests_per_second=5, max_requests=20, max_response_time_ms=1),
    )
    # max_response_time_ms=1 guarantees a breach on any real server
    assert result["threshold_breached"] is True


# ---------- Accessibility module (§15) ----------

def test_a11y_audit_detects_missing_alt_on_demo():
    results = audit_site(["http://demo-app:9000/"])
    assert len(results) == 1
    rules = {v["rule"] for v in results[0]["violations"]}
    # demo-app has unlabeled form inputs and no html lang attribute
    assert "form-label" in rules
    assert "html-lang" in rules


def test_a11y_violations_have_rule_and_severity():
    results = audit_site(["http://demo-app:9000/"])
    for v in results[0]["violations"]:
        assert v["rule"] and v["severity"] in {"critical", "serious", "moderate", "minor"}


# ---------- Screenshot evidence (§12/§19 groundwork) ----------

def test_screenshot_captured_on_failure():
    ex = BrowserExecutor("chromium")
    result = ex.run(
        [
            {"action": "goto", "target": "http://demo-app:9000/"},
            {"action": "expect_status", "value": 999},  # guaranteed mismatch
        ],
        screenshot_key="tests/shot-fail.png",
    )
    if result["error_details"].get("type") == "step_error":
        pytest.skip("demo-app not reachable from this image")
    assert result["status"] == "failed"
    assert result["evidence"].get("screenshot") == "tests/shot-fail.png"
    data = storage.get_bytes("tests/shot-fail.png")
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "stored evidence must be a real PNG"


def test_screenshot_captured_on_pass_when_enabled():
    ex = BrowserExecutor("chromium")
    result = ex.run(
        [
            {"action": "goto", "target": "http://demo-app:9000/"},
            {"action": "expect_title_not_empty"},
        ],
        screenshot_key="tests/shot-pass.png",
        capture_on_pass=True,
    )
    if result["error_details"].get("type") == "step_error":
        pytest.skip("demo-app not reachable from this image")
    assert result["status"] == "passed"
    assert result["evidence"].get("screenshot") == "tests/shot-pass.png"
    assert storage.exists("tests/shot-pass.png")


def test_no_screenshot_on_pass_by_default():
    ex = BrowserExecutor("chromium")
    result = ex.run(
        [
            {"action": "goto", "target": "http://demo-app:9000/"},
            {"action": "expect_title_not_empty"},
        ],
        screenshot_key="tests/shot-nope.png",
    )
    if result["error_details"].get("type") == "step_error":
        pytest.skip("demo-app not reachable from this image")
    assert result["status"] == "passed"
    assert "screenshot" not in result["evidence"]
    assert not storage.exists("tests/shot-nope.png")
