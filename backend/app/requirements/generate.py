"""Requirement → test-case generation (PLAN V2.1, deterministic + optional LLM polish).

Each requirement yields a fixed scenario set (positive, invalid input, missing
data, boundary, injection; email/reset requirements get extra link-expiry and
reuse scenarios). Refs follow `REQ-AUTH-001 → TC-AUTH-001…`. Re-runs are
idempotent: refs that already exist are skipped, not duplicated. Cases are
created unapproved — they enter the normal review gate.

The LLM (when configured) may only refine titles/test-data of the deterministic
scenarios, under a hard time budget, with silent fallback — the same policy as
the assistant (grounded first, LLM polish second).
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import generate_json_budgeted
from app.models import Project, Requirement, RequirementLink, TestCase, TestCategory


def _req_tag(external_id: str) -> str:
    """REQ-AUTH-001 → AUTH-001 (used in TC refs)."""
    tag = external_id.strip().upper()
    if tag.startswith("REQ-"):
        tag = tag[4:]
    return re.sub(r"[^A-Z0-9-]", "", tag) or "REQ"


def _looks_api(requirement: Requirement) -> bool:
    blob = f"{requirement.title}\n{requirement.description}".lower()
    if re.search(r"(POST|PUT|PATCH|DELETE|GET)\s+/\S", blob):
        return True
    return bool(re.search(r"\b(api|endpoint|payload|json response|http status)\b", blob))


def _mentions_email_flow(requirement: Requirement) -> bool:
    blob = f"{requirement.title}\n{requirement.description}".lower()
    return bool(re.search(r"\b(email|otp|reset (link|password)|verification code)\b", blob))


def _base_url(project: Project) -> str:
    return (project.base_url or "").rstrip("/")


def _scenarios(requirement: Requirement, project: Project) -> list[dict]:
    is_api = _looks_api(requirement)
    kind = "api" if is_api else "browser"
    blob = f"{requirement.title}. {requirement.description}".strip()

    if is_api:
        path = "/"
        m = re.search(r"(GET|POST|PUT|PATCH|DELETE)\s+(/\S*)", blob, re.IGNORECASE)
        method, path = (m.group(1).upper(), m.group(2)) if m else ("POST", "/")
        main = {
            "suffix": "001",
            "coverage": "api",
            "kind": "api",
            "title": f"{requirement.title} — happy path ({method} {path} returns success)",
            "steps": [
                {"action": "request", "method": method, "url": "{{base_url}}" + path,
                 "json": {"username": "{{username}}", "password": "{{password}}"}},
                {"action": "expect_status", "value": 200},
            ],
            "expected": f"{method} {path} returns 2xx with a well-formed body",
            "data": {},
        }
        neg_invalid = {
            "suffix": "002",
            "coverage": "negative",
            "kind": "api",
            "title": f"{requirement.title} — invalid input rejected",
            "steps": [
                {"action": "request", "method": method, "url": "{{base_url}}" + path,
                 "json": {"__invalid__": "\"><script>alert(1)</script>"}},
                {"action": "expect_status", "value": 400},
            ],
            "expected": "Malformed/invalid payload is rejected with a 4xx (never 5xx)",
            "data": {},
        }
        neg_missing = {
            "suffix": "003",
            "coverage": "negative",
            "kind": "api",
            "title": f"{requirement.title} — missing required fields rejected",
            "steps": [
                {"action": "request", "method": method, "url": "{{base_url}}" + path, "json": {}},
                {"action": "expect_status", "value": 400},
            ],
            "expected": "Empty payload is rejected with a 4xx (never 5xx)",
            "data": {},
        }
    else:
        url = _base_url(project) or "http://example.invalid"
        main = {
            "suffix": "001",
            "coverage": "ui",
            "kind": "browser",
            "title": f"{requirement.title} — main flow succeeds",
            "steps": [
                {"action": "goto", "url": url},
                {"action": "expect_text", "value": _short_expect(requirement)},
            ],
            "expected": "The flow completes and the expected outcome is visible",
            "data": {},
        }
        neg_invalid = {
            "suffix": "002",
            "coverage": "negative",
            "kind": "browser",
            "title": f"{requirement.title} — invalid input shows a handled error",
            "steps": [
                {"action": "goto", "url": url},
                {"action": "fill", "selector": "input[name='username']", "value": "\"><script>alert(1)</script>"},
                {"action": "click", "selector": "button[type='submit']"},
            ],
            "expected": "A handled validation error is shown; no crash/5xx",
            "data": {},
        }
        neg_missing = {
            "suffix": "003",
            "coverage": "negative",
            "kind": "browser",
            "title": f"{requirement.title} — submitting empty form is rejected",
            "steps": [
                {"action": "goto", "url": url},
                {"action": "click", "selector": "button[type='submit']"},
            ],
            "expected": "Validation prevents submission; user stays on the form",
            "data": {},
        }

    boundary = {
        "suffix": "004",
        "coverage": "boundary",
        "kind": kind,
        "title": f"{requirement.title} — boundary values (min / max / oversized)",
        "steps": list(main["steps"]),
        "expected": "Boundary inputs are accepted up to the documented limit and rejected beyond it",
        "data": {"boundary": ["min", "max", "max+1", "very-long-string…" * 50]},
    }
    security = {
        "suffix": "005",
        "coverage": "security",
        "kind": kind,
        "title": f"{requirement.title} — injection attempt is handled safely",
        "steps": list(neg_invalid["steps"]),
        "expected": "Payload is echoed safely or rejected; no stack trace, no 5xx",
        "data": {"payloads": ["\"><script>alert(1)</script>", "' OR '1'='1", "{{7*7}}"]},
    }

    out = [main, neg_invalid, neg_missing, boundary, security]
    if _mentions_email_flow(requirement):
        out.append({
            "suffix": "006",
            "coverage": "negative",
            "kind": kind,
            "title": f"{requirement.title} — expired reset link is rejected",
            "steps": list(main["steps"]),
            "expected": "An expired token/link is rejected with a clear message",
            "data": {},
        })
        out.append({
            "suffix": "007",
            "coverage": "negative",
            "kind": kind,
            "title": f"{requirement.title} — reset link cannot be reused",
            "steps": list(main["steps"]),
            "expected": "Reusing a consumed token fails; the user must request a new one",
            "data": {},
        })
    return out


_CATEGORY_MAP = {
    "ui": TestCategory.functional,
    "api": TestCategory.api,
    "negative": TestCategory.functional,  # closest enum; coverage keeps the nuance
    "boundary": TestCategory.functional,
    "security": TestCategory.security,
}

def _short_expect(requirement: Requirement) -> str:
    words = re.sub(r"[^\w\s]", " ", requirement.title).split()
    return " ".join(words[:6]) or "the page"


def _llm_polish(requirement: Requirement, scenarios: list[dict]) -> list[dict]:
    """Optionally let the LLM refine titles/data. Deterministic set on any failure."""
    system = (
        "You are a senior QA analyst. Improve the given test scenarios for one requirement: "
        "sharpen scenario titles and make test_data concrete. NEVER invent new scenario kinds, "
        "change suffixes, kinds, or add credentials. Respond with JSON: "
        '{"scenarios": [{"suffix": "...", "title": "...", "expected": "...", "data": {}}]}.'
    )
    payload = {
        "requirement": {"id": requirement.external_id, "title": requirement.title,
                         "description": requirement.description[:1500]},
        "scenarios": [{"suffix": s["suffix"], "title": s["title"],
                       "expected": s["expected"], "data": s["data"]} for s in scenarios],
    }
    result = generate_json_budgeted(
        system, repr(payload), fallback=None, budget_seconds=30.0
    )
    if not isinstance(result, dict):
        return scenarios
    polished = {s.get("suffix"): s for s in result.get("scenarios", []) if isinstance(s, dict)}
    for s in scenarios:
        p = polished.get(s["suffix"])
        if not p:
            continue
        if isinstance(p.get("title"), str) and p["title"].strip():
            s["title"] = p["title"].strip()[:300]
        if isinstance(p.get("expected"), str) and p["expected"].strip():
            s["expected"] = p["expected"].strip()[:500]
        if isinstance(p.get("data"), dict):
            s["data"] = p["data"]
    return scenarios


def generate_cases_for_requirement(
    db: Session, project: Project, requirement: Requirement, use_llm: bool = False
) -> tuple[list[TestCase], list[str]]:
    """Generate (unapproved) cases + links for one requirement.

    Returns (created_cases, skipped_refs). Idempotent: existing refs are skipped.
    """
    tag = _req_tag(requirement.external_id)
    scenarios = _scenarios(requirement, project)
    if use_llm:
        scenarios = _llm_polish(requirement, scenarios)

    existing_refs = set(
        db.execute(
            select(TestCase.ref).where(
                TestCase.project_id == project.id,
                TestCase.ref.like(f"TC-{tag}-%"),
            )
        ).scalars()
    )

    created: list[TestCase] = []
    skipped: list[str] = []
    counter = 0
    for s in scenarios:
        counter += 1
        ref = f"TC-{tag}-{s['suffix']}"
        if ref in existing_refs:
            skipped.append(ref)
            continue
        case = TestCase(
            project_id=project.id,
            ref=ref,
            scenario=s["title"],
            module=f"req:{requirement.external_id}",
            # coverage kinds (ui/api/negative/boundary/security) → TestCategory enum
            category=_CATEGORY_MAP.get(s["coverage"], TestCategory.functional),
            priority=requirement.priority,
            preconditions=f"Requirement {requirement.external_id}: {requirement.title}",
            test_data=s["data"],
            steps=s["steps"],
            expected_result=s["expected"],
            kind=s["kind"],
            enabled=True,
            approved=False,  # review gate
        )
        db.add(case)
        created.append(case)

    db.flush()  # assign case ids before linking
    for case in created:
        coverage = next(
            (s["coverage"] for s in scenarios if f"TC-{tag}-{s['suffix']}" == case.ref),
            "ui",
        )
        db.add(RequirementLink(
            project_id=project.id,
            requirement_id=requirement.id,
            test_case_id=case.id,
            coverage=coverage,
        ))
    db.commit()
    return created, skipped
