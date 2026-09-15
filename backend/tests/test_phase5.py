"""Phase 5 unit tests: test case generation logic (spec §7, §8)."""
from app.generation.testcases import generate_cases, tag_suites


PAGES = [
    {"url": "http://demo-app:9000/", "title": "Home", "depth": 0, "components": {"forms": 1}},
    {"url": "http://demo-app:9000/login", "title": "Login", "depth": 1, "components": {"forms": 1, "inputs": 2}},
    {"url": "http://demo-app:9000/users", "title": "Users", "depth": 1, "components": {}},
]

APIS = [
    {"method": "GET", "path": "/api/users", "group": "users"},
    {"method": "POST", "path": "/api/checkout", "group": "orders"},
]


def test_cases_have_all_required_fields():
    cases = generate_cases("http://demo-app:9000", PAGES, APIS, has_credentials=False)
    required = {"ref", "scenario", "module", "category", "priority", "preconditions",
                "test_data", "steps", "expected_result", "kind"}
    for c in cases:
        assert required <= set(c.keys()), f"missing fields in {c.get('ref')}"


def test_positive_and_negative_generated():
    cases = generate_cases("http://demo-app:9000", PAGES, APIS, has_credentials=False)
    positives = [c for c in cases if c["positive"]]
    negatives = [c for c in cases if not c["positive"]]
    assert positives, "no positive cases"
    assert negatives, "no negative cases"


def test_login_cases_generated_when_login_page_present():
    cases = generate_cases("http://demo-app:9000", PAGES, [], has_credentials=True)
    login_cases = [c for c in cases if c["module"] == "login"]
    assert len(login_cases) >= 3  # valid + empty + wrong-password
    assert any(c["priority"] == "critical" for c in login_cases)


def test_api_cases_match_inventory():
    cases = generate_cases("http://demo-app:9000", PAGES, APIS, has_credentials=False)
    api_cases = [c for c in cases if c["kind"] == "api"]
    paths = {c["test_data"].get("path") for c in api_cases}
    assert "/api/users" in paths
    assert "/api/checkout" in paths


def test_unique_refs():
    cases = generate_cases("http://demo-app:9000", PAGES, PAGES + APIS, has_credentials=False)
    refs = [c["ref"] for c in cases]
    assert len(refs) == len(set(refs))
    assert all(r.startswith("TC-") for r in refs)


def test_suites_tagging():
    cases = generate_cases("http://demo-app:9000", PAGES, APIS, has_credentials=False)
    suites = tag_suites(cases)
    assert set(suites.keys()) == {"smoke", "regression"}
    assert set(suites["smoke"]) <= set(suites["regression"])  # smoke ⊂ regression
    assert suites["smoke"], "smoke suite should contain critical cases"


def test_negative_cases_expect_graceful_handling():
    cases = generate_cases("http://demo-app:9000", PAGES, APIS, has_credentials=False)
    for c in cases:
        if not c["positive"]:
            assert ("never HTTP 5xx" in c["expected_result"]) or ("never a server error" in c["expected_result"])
