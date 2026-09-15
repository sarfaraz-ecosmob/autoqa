"""AI test-case generation (spec §7).

Every case carries the full spec §7 field set and is grounded in discovery
evidence (pages, APIs, login/forms detection). Positive AND negative cases
are generated. Uses the pluggable LLM when configured; deterministic
heuristic generation otherwise.
"""
import json

from app.ai import generate_json

VALID_PRIORITIES = {"critical", "high", "medium", "low"}


def _case(
    ref: str,
    scenario: str,
    module: str,
    category: str,
    priority: str,
    preconditions: str,
    test_data: dict,
    steps: list[dict],
    expected_result: str,
    kind: str,
) -> dict:
    return {
        "ref": ref,
        "scenario": scenario,
        "module": module,
        "category": category,
        "priority": priority,
        "preconditions": preconditions,
        "test_data": test_data,
        "steps": steps,
        "expected_result": expected_result,
        "kind": kind,  # browser | api
        "positive": True,
    }


def _neg(*args, **kwargs) -> dict:
    case = _case(*args, **kwargs)
    case["positive"] = False
    return case


class _RefCounter:
    def __init__(self):
        self.counts: dict[str, int] = {}

    def next(self, module: str) -> str:
        self.counts[module] = self.counts.get(module, 0) + 1
        return f"TC-{module.upper()}-{self.counts[module]:03d}"


def generate_cases(
    base_url: str,
    pages: list[dict],
    apis: list[dict],
    has_credentials: bool,
) -> list[dict]:
    """Deterministic, evidence-grounded case generation (spec §7 fields)."""
    refs = _RefCounter()
    cases: list[dict] = []

    # --- Page navigation / UI (positive) ---
    nav_pages = [p for p in pages if p.get("url", "").rstrip("/") != base_url.rstrip("/")]
    for page in pages[:40]:
        url = page.get("url", base_url)
        title = page.get("title", "")
        cases.append(
            _case(
                refs.next("nav"),
                f"Open {url} and verify the page loads with expected content",
                module="navigation",
                category="ui",
                priority="high" if page.get("depth", 9) <= 1 else "medium",
                preconditions="Application is reachable",
                test_data={"url": url},
                steps=[
                    {"action": "goto", "target": url},
                    {"action": "expect_status", "value": 200},
                    {"action": "expect_title_not_empty"},
                ],
                expected_result=f"Page loads with status 200 and a non-empty title{f' ({title})' if title else ''}",
                kind="browser",
            )
        )

    # --- Login flows (positive + negative) ---
    login_pages = [p for p in pages if "login" in p.get("url", "").lower()]
    if login_pages:
        login_url = login_pages[0]["url"]
        creds = {"username": "demo_user", "password": "DemoPass123!"} if has_credentials else {
            "username": "demo_user",
            "password": "demo_pass",
        }
        cases.append(
            _case(
                refs.next("login"),
                "Verify successful login with valid credentials",
                module="login",
                category="functional",
                priority="critical",
                preconditions="A valid user account exists",
                test_data=creds,
                steps=[
                    {"action": "goto", "target": login_url},
                    {"action": "fill", "target": "input[name='username']", "value": creds["username"]},
                    {"action": "fill", "target": "input[name='password']", "value": creds["password"]},
                    {"action": "click", "target": "button[type='submit']"},
                    {"action": "expect_response", "contains": "ok"},
                ],
                expected_result="Login succeeds and the application indicates a successful session",
                kind="browser",
            )
        )
        cases.append(
            _neg(
                refs.next("login"),
                "Verify login with empty credentials is rejected gracefully",
                module="login",
                category="security",
                priority="high",
                preconditions="Login page is reachable",
                test_data={"username": "", "password": ""},
                steps=[
                    {"action": "goto", "target": login_url},
                    {"action": "click", "target": "button[type='submit']"},
                ],
                expected_result="Submission is rejected with a client-side validation message or 4xx response — never a server error",
                kind="browser",
            )
        )
        cases.append(
            _neg(
                refs.next("login"),
                "Verify login with an invalid password does not produce a server error",
                module="login",
                category="security",
                priority="critical",
                preconditions="Login page is reachable",
                test_data={"username": "demo_user", "password": "definitely-wrong"},
                steps=[
                    {"action": "goto", "target": login_url},
                    {"action": "fill", "target": "input[name='username']", "value": "demo_user"},
                    {"action": "fill", "target": "input[name='password']", "value": "definitely-wrong"},
                    {"action": "click", "target": "button[type='submit']"},
                    {"action": "expect_no_server_error"},
                ],
                expected_result="Invalid password yields a handled error (4xx or message), never HTTP 5xx",
                kind="browser",
            )
        )

    # --- API tests (positive) from observed/inventory endpoints ---
    for api in apis[:40]:
        method = api.get("method", "GET")
        path = api.get("path", "/")
        module = api.get("group", "general")
        expected_status = 200 if method in ("GET", "HEAD", "OPTIONS") else None
        cases.append(
            _case(
                refs.next("api"),
                f"Verify {method} {path} responds correctly",
                module=module,
                category="api",
                priority="critical" if module in ("authentication", "orders") else "high",
                preconditions="API is reachable from the execution environment",
                test_data={"path": path},
                steps=[
                    {"action": "request", "method": method, "path": path},
                    {"action": "expect_status", "value": expected_status if expected_status else "2xx"},
                    {"action": "expect_response_time_under_ms", "value": 3000},
                ],
                expected_result=f"{method} {path} returns {expected_status or '2xx'} with a well-formed body within 3s",
                kind="api",
            )
        )

    # --- Forms (negative) ---
    form_pages = [p for p in pages if p.get("components", {}).get("forms", 0) > 0 and "login" not in p.get("url", "")]
    for page in form_pages[:10]:
        cases.append(
            _neg(
                refs.next("form"),
                f"Submit the form on {page.get('url')} with empty values and verify graceful handling",
                module="forms",
                category="functional",
                priority="medium",
                preconditions=f"Form page {page.get('url')} is reachable",
                test_data={"url": page.get("url")},
                steps=[
                    {"action": "goto", "target": page.get("url")},
                    {"action": "click", "target": "button[type='submit']"},
                    {"action": "expect_no_server_error"},
                ],
                expected_result="Empty submission is handled (validation message or 4xx), never HTTP 5xx",
                kind="browser",
            )
        )

    # --- Error handling ---
    cases.append(
        _case(
            refs.next("err"),
            "Verify a non-existent route returns a proper 404 without leaking internals",
            module="errors",
            category="security",
            priority="medium",
            preconditions="Application is reachable",
            test_data={"path": "/definitely-not-a-page-xyz"},
            steps=[
                {"action": "request", "method": "GET", "path": "/definitely-not-a-page-xyz"},
                {"action": "expect_status", "value": 404},
            ],
            expected_result="404 response with a user-friendly error (no stack trace)",
            kind="api",
        )
    )

    return cases


def llm_refine_cases(cases: list[dict]) -> list[dict]:
    """Optionally let the LLM extend/polish cases. Falls back unchanged."""
    def fallback():
        return cases

    refined = generate_json(
        system=(
            "You are a senior QA engineer. Given JSON test cases, return an "
            "improved JSON array with the same keys, adding missing negative "
            "cases where valuable. Do not invent URLs that are not present."
        ),
        user=json.dumps(cases[:50]),
        fallback=fallback(),
    )
    if not isinstance(refined, list):
        return cases
    return [c for c in refined if isinstance(c, dict)]


def tag_suites(cases: list[dict]) -> dict[str, list[str]]:
    """Smoke = critical cases; regression = everything (spec §18 filters)."""
    smoke = [c["ref"] for c in cases if c.get("priority") == "critical"]
    regression = [c["ref"] for c in cases]
    return {"smoke": smoke, "regression": regression}
