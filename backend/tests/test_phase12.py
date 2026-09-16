"""Phase 12 unit tests: report collector + five renderers (spec §19)."""
import json

from app.reports.collector import collect_report_data
from app.reports.renderers import RENDERERS


def _dataset() -> dict:
    """Synthetic §19 dataset — no DB required for renderer tests."""
    return {
        "generated_at": "2026-09-16T00:00:00+00:00",
        "scope": {"project_id": "p1", "test_run_id": None},
        "application": {
            "name": "Demo Shop",
            "base_url": "http://demo-app:9000",
            "authorization_confirmed": True,
            "frontend_stack": {"framework": "static_html"},
            "backend_stack": {"api_styles": ["rest"]},
            "pages_discovered": 7,
            "apis_discovered": 5,
        },
        "environment": {"platform": "AutoQA Docker Compose", "browsers": ["chromium", "firefox"],
                        "generated_by_user": "tester"},
        "test_plan": {
            "version": 1, "objective": "Verify demo shop", "scope": {},
            "sections": [{"section": "Functional", "content": "core flows", "priority": "high"}],
            "approved": True, "generated_by": "heuristic",
        },
        "runs": [],
        "executions": [
            {"ref": "TC-LOGIN-001", "scenario": "Valid login", "module": "authentication",
             "category": "functional", "priority": "critical", "kind": "browser",
             "browser": "chromium", "status": "passed", "attempt": 1, "duration_ms": 812,
             "worker_id": "bw-1", "actual_result": "all steps passed", "error_type": None,
             "screenshot_key": None, "console": [], "step_log": []},
            {"ref": "TC-FORM-001", "scenario": "Submit search form", "module": "forms",
             "category": "functional", "priority": "high", "kind": "browser",
             "browser": "firefox", "status": "failed", "attempt": 1, "duration_ms": 5210,
             "worker_id": "bw-2", "actual_result": "step_error: TimeoutError",
             "error_type": "step_error", "screenshot_key": None, "console": ["error: x"], "step_log": []},
        ],
        "counters": {"total": 2, "passed": 1, "failed": 1, "skipped": 0, "blocked": 0,
                     "running": 0, "queued": 0, "done": 2, "pass_rate": 50.0},
        "defects": [{"title": "TC-FORM-001: submit fails", "severity": "high", "module": "forms",
                     "description": "Timeout on submit", "possible_root_cause": "Selector drift",
                     "recommended_investigation": "Check page structure", "status": "open"}],
        "security_findings": [{"title": "Missing security header: content-security-policy",
                               "severity": "medium", "description": "security_headers: observed",
                               "evidence_url": "http://demo-app:9000", "remediation": "Add CSP",
                               "status": "open"}],
        "performance": [{"scenario": "/health", "concurrent_users": 2, "p50_ms": 3.7, "p90_ms": 4.9,
                         "p95_ms": 5.6, "p99_ms": 7.5, "throughput_rps": 8.8, "error_rate": 0.0,
                         "threshold_breached": False, "created_at": "2026-09-16T00:00:00+00:00"}],
        "accessibility": [{"page_url": "http://demo-app:9000/", "violation_count": 2,
                           "by_severity": {"critical": 1, "serious": 1},
                           "rules": ["form-label", "html-lang"]}],
        "executive_summary": ["Executed 2 test case executions.", "Pass rate: 50.0%."],
        "recommendations": ["Investigate the 1 failed execution(s)."],
    }


# ---------- all renderers produce bytes + correct content type ----------

def test_all_five_formats_registered():
    assert set(RENDERERS) == {"csv", "xlsx", "pdf", "html", "json"}


def test_csv_content():
    content, ctype = RENDERERS["csv"](_dataset())
    assert ctype == "text/csv"
    text = content.decode()
    assert "TC-LOGIN-001" in text and "TC-FORM-001" in text
    assert "passed" in text and "failed" in text


def test_json_roundtrip():
    content, ctype = RENDERERS["json"](_dataset())
    assert ctype == "application/json"
    parsed = json.loads(content)
    assert parsed["counters"]["pass_rate"] == 50.0
    assert parsed["executions"][1]["ref"] == "TC-FORM-001"


def test_xlsx_magic_bytes():
    content, ctype = RENDERERS["xlsx"](_dataset())
    # XLSX is a ZIP container
    assert content[:4] == b"PK\x03\x04"
    assert "spreadsheetml" in ctype


def test_pdf_magic_bytes():
    content, ctype = RENDERERS["pdf"](_dataset())
    assert content[:5] == b"%PDF-"
    assert ctype == "application/pdf"


def test_html_contains_all_spec_sections():
    content, ctype = RENDERERS["html"](_dataset())
    html = content.decode()
    assert ctype == "text/html"
    for section in ("Executive Summary", "Application Information", "Test Plan",
                    "Execution Summary", "Test Cases", "Defects", "Security Findings",
                    "Performance Results", "Accessibility Results", "Recommendations"):
        assert section in html, f"missing §19 section: {section}"
    assert "TC-LOGIN-001" in html and "TC-FORM-001" in html
    assert "Add CSP" in html


def test_html_escapes_untrusted_content():
    data = _dataset()
    data["executions"][0]["actual_result"] = "<script>alert(1)</script>"
    content, _ = RENDERERS["html"](data)
    html = content.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_html_embeds_screenshot_when_key_present():
    """Full collector→renderer path with a real stored screenshot."""
    import io as _io
    import uuid

    from app import storage

    from PIL import Image as PILImage  # reportlab dependency

    buf = _io.BytesIO()
    PILImage.new("RGB", (8, 8), color=(200, 60, 60)).save(buf, format="PNG")
    png = buf.getvalue()
    key = f"report-selftest-{uuid.uuid4().hex[:8]}/shot.png"
    storage.put_bytes(key, png, "image/png")

    data = _dataset()
    data["executions"][1]["screenshot_key"] = key
    content, _ = RENDERERS["html"](data)
    html = content.decode()
    assert "data:image/png;base64," in html

    pdf, _ = RENDERERS["pdf"](data)
    assert pdf[:5] == b"%PDF-"

    storage.delete(key)
