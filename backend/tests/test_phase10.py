"""Phase 10 unit tests: security scanner logic (spec §13)."""
from app.security.scan import Finding, ScanReport, run_scan


def test_report_counts():
    report = ScanReport(tier="passive", target="http://x")
    report.findings = [
        Finding("a", "medium", "security_headers", {}, "fix"),
        Finding("b", "medium", "cookie_security", {}, "fix"),
        Finding("c", "high", "cors", {}, "fix"),
    ]
    assert report.counts() == {"medium": 2, "high": 1}


def test_passive_scan_flags_missing_headers():
    report = run_scan("http://demo-app:9000", tier="passive")
    titles = [f.title for f in report.findings]
    assert any("content-security-policy" in t for t in titles)
    assert any("Missing security header" in t for t in titles)


def test_safe_active_detects_reflection_on_demo():
    """demo-app /search reflects input raw (deliberate flaw)."""
    report = run_scan("http://demo-app:9000", tier="safe_active")
    categories = {f.category for f in report.findings}
    assert "xss_indicator" in categories


def test_full_scan_requires_authorization():
    """Worker task guard: full tier without authorization fails closed."""
    import uuid

    from app.worker_security import run_security_scan

    result = run_security_scan.apply(
        kwargs={
            "scan_id": uuid.uuid4().hex,
            "progress_channel": "test:full-scan",
            "project_id": "nonexistent-project",
            "base_url": "http://demo-app:9000",
            "tier": "full",
        }
    )
    # nonexistent project → failed regardless of tier (fail closed)
    assert result.get()["status"] == "failed"


def test_scan_findings_carry_evidence_and_remediation():
    report = run_scan("http://demo-app:9000", tier="passive")
    for f in report.findings:
        assert f.evidence, "findings must carry evidence"
        assert f.remediation, "findings must carry remediation"
        assert f.severity in {"critical", "high", "medium", "low"}
