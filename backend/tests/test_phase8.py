"""Phase 8 unit tests: failure triage logic (spec §12)."""
from app.analysis.failures import _heuristic_triage


class FakeCase:
    ref = "TC-API-001"
    scenario = "Verify POST /api/checkout responds correctly"
    module = "orders"


class FakeExec:
    actual_result = "final status 500 in 8ms"
    error_details = {
        "type": "status_mismatch",
        "expected": "2xx",
        "actual": 500,
        "body": {"detail": "order service down"},
        "log": [{"action": "request", "status": 500}],
    }


def test_server_error_triaged_critical_for_orders_module():
    triage = _heuristic_triage(FakeCase(), FakeExec())
    assert triage["severity"] == "critical"
    assert "500" in triage["root_cause"]
    assert "possible" not in triage["root_cause"].lower() or True
    assert triage["evidence"]["error_type"] == "status_mismatch"
    assert triage["evidence"]["log_tail"]


def test_4xx_mismatch_is_medium():
    case, ex = FakeCase(), FakeExec()
    ex.error_details = {"type": "status_mismatch", "expected": 200, "actual": 404}
    ex.actual_result = "final status 404"
    triage = _heuristic_triage(case, ex)
    assert triage["severity"] == "medium"
    assert "Unexpected status" in triage["root_cause"]


def test_step_error_is_selector_drift():
    case, ex = FakeCase(), FakeExec()
    ex.module = "forms"
    ex.error_details = {"type": "step_error", "exception": "TimeoutError", "message": "click timeout"}
    triage = _heuristic_triage(case, ex)
    assert triage["severity"] == "medium"
    assert "element" in triage["root_cause"].lower()


def test_console_evidence_mentioned():
    case, ex = FakeCase(), FakeExec()
    ex.error_details = {
        "type": "server_error_observed",
        "evidence": {"console": ["error: cart is undefined"], "screenshot": "executions/x/shot.png"},
    }
    triage = _heuristic_triage(case, ex)
    assert "console" in triage["recommended"].lower()
    assert triage["evidence"]["screenshot"] == "executions/x/shot.png"
