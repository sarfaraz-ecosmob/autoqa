"""Requirements & Traceability (PLAN V2.1): ingestion, generation, matrix.

Unit-style tests on in-memory sqlite (same pattern as test_tier1).
"""
import io
import json

import pytest

from app.requirements.generate import _req_tag, generate_cases_for_requirement
from app.requirements.ingest import (
    RequirementDraft,
    dedupe_and_normalize,
    parse_document,
)
from app.requirements.traceability import build_matrix, latest_results_per_case


# ---------------------------------------------------------------- helpers

def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture()
def project_db():
    from datetime import datetime, timezone

    from app.models import Project, User

    db = _session()
    user = User(email="req-owner@example.com", password_hash="x", role="member")
    db.add(user)
    db.flush()
    project = Project(
        name="ReqProj", base_url="http://demo.test", description="",
        owner_id=user.id, authorization_confirmed=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(project)
    db.commit()
    yield db, project


def _add_requirement(db, project, external_id, title, description=""):
    from app.models import Requirement

    req = Requirement(project_id=project.id, external_id=external_id, title=title,
                      description=description, source="manual")
    db.add(req)
    db.commit()
    return req


# ---------------------------------------------------------------- ingest

def test_parse_md_headings_and_req_tokens():
    md = (
        "# REQ-AUTH-001: Password reset via email\n"
        "User can reset using the registered email. POST /api/reset\n"
        "\n"
        "# REQ-AUTH-002: Reset link single use\n"
        "Links cannot be reused.\n"
    )
    drafts, skipped = dedupe_and_normalize(parse_document("brd.md", md.encode()))
    assert skipped == []
    assert [d.external_id for d in drafts] == ["REQ-AUTH-001", "REQ-AUTH-002"]
    assert "reset" in drafts[0].title.lower()
    assert "POST /api/reset" in drafts[0].description


def test_parse_plain_text_blank_line_sections_with_inline_id():
    txt = (
        "REQ-PAY-010: Declined card shows handled error\n"
        "Use sandbox decline card.\n"
        "\n"
        "Dashboard loads under two seconds for 95 percent of users.\n"
    )
    drafts, _ = dedupe_and_normalize(parse_document("reqs.txt", txt.encode()))
    assert drafts[0].external_id == "REQ-PAY-010"
    assert "Declined card" in drafts[0].title
    # second section has no REQ token → auto-numbered
    assert drafts[1].external_id == "REQ-GEN-001"


def test_parse_jira_json_export():
    payload = {
        "issues": [
            {"key": "QA-12", "fields": {"summary": "Login works",
                                         "description": "h3. Steps\r\n * open /login"}},
            {"key": "QA-13", "fields": {"summary": "Logout works", "description": None}},
        ]
    }
    drafts, _ = dedupe_and_normalize(parse_document("jira.json", json.dumps(payload).encode()))
    assert [d.external_id for d in drafts] == ["QA-12", "QA-13"]
    assert drafts[0].source == "jira"
    assert drafts[0].source_ref == "QA-12"
    assert "open /login" in drafts[0].description
    assert "h3." not in drafts[0].description


def test_parse_csv_header_aliases():
    csv_text = "key,summary,detail\nREQ-X-1,First req,Body one\nREQ-X-2,Second req,\n"
    drafts, _ = dedupe_and_normalize(parse_document("reqs.csv", csv_text.encode()))
    assert [d.external_id for d in drafts] == ["REQ-X-1", "REQ-X-2"]
    assert drafts[0].title == "First req"


def test_parse_xlsx_roundtrip():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["id", "title", "description"])
    ws.append(["REQ-S-1", "Sheet req", "from xlsx"])
    buf = io.BytesIO()
    wb.save(buf)
    drafts, _ = dedupe_and_normalize(parse_document("reqs.xlsx", buf.getvalue()))
    assert [d.external_id for d in drafts] == ["REQ-S-1"]
    assert drafts[0].description == "from xlsx"


def test_unsupported_type_raises():
    with pytest.raises(ValueError):
        parse_document("reqs.doc", b"whatever")


def test_dedupe_reports_duplicates_and_keeps_existing():
    drafts = [
        RequirementDraft("REQ-A-1", "one", ""),
        RequirementDraft("REQ-A-1", "dup", ""),
        RequirementDraft("", "", "title only from description line"),
    ]
    kept, skipped = dedupe_and_normalize(drafts, existing_ids={"REQ-A-1"})
    # first is skipped (exists in DB), second is skipped (in-doc duplicate)
    assert skipped == ["REQ-A-1", "REQ-A-1"]
    assert [d.external_id for d in kept] == ["REQ-GEN-001"]


# ---------------------------------------------------------------- generate

def test_req_tag_strips_prefix():
    assert _req_tag("REQ-AUTH-001") == "AUTH-001"
    assert _req_tag("REQ-99") == "99"


def test_generation_creates_scenarios_and_links(project_db):
    db, project = project_db
    req = _add_requirement(db, project, "REQ-AUTH-001",
                           "User can reset password using registered email",
                           "Uses email; POST /api/reset-password")
    cases, skipped = generate_cases_for_requirement(db, project, req, use_llm=False)
    assert skipped == []
    refs = [c.ref for c in cases]
    assert refs[0] == "TC-AUTH-001-001"
    assert len(cases) == 7  # 5 base + expired-link + link-reuse (email requirement)
    kinds = {c.kind for c in cases}
    assert kinds == {"api"}  # description mentions POST /api/...
    assert all(not c.approved for c in cases)  # review gate
    assert all("{{password}}" not in json.dumps(c.steps) or "{{username}}" in json.dumps(c.steps)
               for c in cases)  # placeholders only, no plaintext

    links = req.links
    assert len(links) == 7
    coverages = {l.coverage for l in links}
    assert {"api", "negative", "boundary", "security"} <= coverages


def test_generation_is_idempotent(project_db):
    db, project = project_db
    req = _add_requirement(db, project, "REQ-DASH-001", "Dashboard shows order summary",
                           "Authenticated users see orders")
    first, _ = generate_cases_for_requirement(db, project, req, use_llm=False)
    second, skipped = generate_cases_for_requirement(db, project, req, use_llm=False)
    assert second == []
    assert sorted(skipped) == sorted(c.ref for c in first)
    assert db.query(type(req).id).count() >= 1


def test_generation_ui_vs_api_kind(project_db):
    db, project = project_db
    ui_req = _add_requirement(db, project, "REQ-UI-001", "User can browse the catalog",
                              "Products are listed with images")
    cases, _ = generate_cases_for_requirement(db, project, ui_req, use_llm=False)
    assert {c.kind for c in cases} == {"browser"}


# ---------------------------------------------------------------- traceability

def test_latest_results_and_matrix(project_db):
    from datetime import datetime, timedelta, timezone

    from app.models import (
        Defect,
        RunStatus,
        TestExecution,
        TestRun,
        TestStatus,
    )

    db, project = project_db
    req = _add_requirement(db, project, "REQ-M-001", "Requirement M", "")
    cases, _ = generate_cases_for_requirement(db, project, req, use_llm=False)
    base = datetime.now(timezone.utc)

    run = TestRun(project_id=project.id, label="run-1", status=RunStatus.completed, created_at=base)
    db.add(run)
    db.flush()
    # case 0: older FAIL then newer PASS → latest = passed
    # case 1: only FAIL → failed
    # case 2: never executed → never-run
    db.add(TestExecution(test_run_id=run.id, test_case_id=cases[0].id, browser="chromium",
                         status=TestStatus.failed, attempt=1,
                         created_at=base - timedelta(minutes=5)))
    db.add(TestExecution(test_run_id=run.id, test_case_id=cases[0].id, browser="chromium",
                         status=TestStatus.passed, attempt=1,
                         created_at=base))
    db.add(TestExecution(test_run_id=run.id, test_case_id=cases[1].id, browser="chromium",
                         status=TestStatus.failed, attempt=1, created_at=base))
    # a defect on case 1's execution
    db.flush()
    db.add(Defect(project_id=project.id, execution_id=None, title="manual defect", severity="medium"))
    db.commit()

    latest = latest_results_per_case(db, str(project.id))
    assert latest[cases[0].id] == "passed"
    assert latest[cases[1].id] == "failed"
    assert cases[2].id not in latest

    matrix = build_matrix(db, project)
    assert matrix["summary"]["requirements"] == 1
    assert matrix["summary"]["covered"] == 1
    assert matrix["summary"]["uncovered"] == 0
    row = matrix["rows"][0]
    assert row["external_id"] == "REQ-M-001"
    results = row["results"]
    assert results[0] == "passed" and results[1] == "failed" and results[2] == "never-run"
    # pass_rate = 1 passed / 2 executed
    assert matrix["summary"]["pass_rate"] == pytest.approx(0.5)


def test_uncovered_requirement_shows_in_matrix(project_db):
    db, project = project_db
    _add_requirement(db, project, "REQ-LONELY-001", "No tests for me", "")
    matrix = build_matrix(db, project)
    assert matrix["summary"] == {"requirements": 1, "covered": 0, "uncovered": 1, "pass_rate": None}
    assert matrix["rows"][0]["cases"] == []
    assert matrix["rows"][0]["results"] == []
