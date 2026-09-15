"""Phase 4 unit tests: test plan generation logic."""
from app.planning.generator import ALL_SECTIONS, build_test_plan, _sections_from_evidence


def test_all_22_sections_defined():
    assert len(ALL_SECTIONS) == 22
    assert "Functional testing" in ALL_SECTIONS
    assert "Security testing" in ALL_SECTIONS


def test_plan_includes_login_sections_when_login_page_present():
    pages = [{"url": "http://x/login", "components": {}}]
    sections = _sections_from_evidence(pages, [])
    names = {s["section"] for s in sections}
    assert "Authentication testing" in names
    assert "Session management" in names


def test_plan_omits_upload_section_without_uploads():
    pages = [{"url": "http://x/", "components": {"file_uploads": 0}}]
    sections = _sections_from_evidence(pages, [])
    assert "File upload/download testing" not in {s["section"] for s in sections}


def test_plan_includes_upload_section_when_detected():
    pages = [{"url": "http://x/upload", "components": {"file_uploads": 2}}]
    sections = _sections_from_evidence(pages, [])
    assert "File upload/download testing" in {s["section"] for s in sections}


def test_priorities_are_valid():
    pages = [{"url": "http://x/login", "components": {"forms": 1, "file_uploads": 1}}]
    apis = [{"method": "GET", "path": "/api/users", "group": "users"}]
    sections = _sections_from_evidence(pages, apis)
    valid = {"critical", "high", "medium", "low"}
    assert all(s["priority"] in valid for s in sections)
    assert all(s["category"] in {"functional", "ui", "api", "integration", "security",
                                 "performance", "accessibility", "compatibility", "regression"}
               for s in sections)


def test_build_test_plan_heuristic_shape():
    plan = build_test_plan(
        project_name="Demo",
        base_url="http://demo-app:9000",
        pages=[{"url": "http://demo-app:9000/login", "components": {"forms": 1}}],
        apis=[{"method": "GET", "path": "/api/users", "group": "users"}],
        frontend={"frameworks": ["static-html"], "css": [], "evidence": {}},
    )
    assert "objective" in plan
    assert len(plan["sections"]) >= 18
    names = {s["section"] for s in plan["sections"]}
    assert {"Functional testing", "UI testing", "API testing", "Security testing"} <= names


def test_llm_unconfigured_returns_heuristic(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_provider", "none")
    plan = build_test_plan("P", "http://x", pages=[], apis=[], frontend={})
    assert isinstance(plan, dict) and "sections" in plan
