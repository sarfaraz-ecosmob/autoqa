"""Phase 3 unit tests: analysis logic (no network)."""
from app.discovery.analyze import (
    build_architecture_map,
    classify_api_group,
    detect_frontend,
    parse_openapi,
    summarize_requests,
)


# ---------- frontend fingerprinting ----------

def test_detect_react_evidence():
    stack = detect_frontend(
        page_sources=["<div id=root data-reactroot></div><script>react-dom</script>"],
        script_urls=[],
    )
    assert "React" in stack["frameworks"]
    assert "React" in stack["evidence"]


def test_detect_nextjs_and_tailwind():
    stack = detect_frontend(
        page_sources=["<script id='__NEXT_DATA__'>{}</script>"],
        script_urls=["/_next/static/chunk.js"],
        css_urls=["/_next/static/css/tailwind.css"],
    )
    assert "Next.js" in stack["frameworks"]
    assert "Tailwind CSS" in stack["css"]


def test_no_weak_fingerprint_claims():
    """Only static-html fallback when there is zero evidence (spec §4)."""
    stack = detect_frontend(page_sources=["<html><body>plain</body></html>"], script_urls=[])
    assert stack["frameworks"] == ["static-html"]
    assert stack["css"] == []


# ---------- API grouping ----------

def test_classify_groups():
    assert classify_api_group("POST", "http://x/api/login") == "authentication"
    assert classify_api_group("GET", "http://x/api/products/1") == "products"
    assert classify_api_group("POST", "http://x/api/checkout") == "orders"
    assert classify_api_group("GET", "http://x/api/misc") == "general"


def test_summarize_requests_skips_assets_and_third_party():
    rows = summarize_requests(
        [
            {"method": "GET", "url": "http://app:9000/api/users", "resource_type": "xhr", "status": 200},
            {"method": "GET", "url": "http://app:9000/static/logo.png", "resource_type": "image", "status": 200},
            {"method": "GET", "url": "http://cdn.third-party.com/lib.js", "resource_type": "script", "status": 200},
            {"method": "GET", "url": "http://app:9000/login", "resource_type": "document", "status": 200},
        ],
        base_url="http://app:9000",
    )
    assert len(rows) == 1
    assert rows[0]["path"] == "/api/users"
    assert rows[0]["group"] == "users"


def test_summarize_dedupes_by_method_path():
    rows = summarize_requests(
        [
            {"method": "GET", "url": "http://app:9000/api/users", "resource_type": "xhr", "status": 200},
            {"method": "GET", "url": "http://app:9000/api/users", "resource_type": "fetch", "status": 200},
            {"method": "GET", "url": "http://app:9000/api/users?page=2", "resource_type": "fetch", "status": 200},
        ],
        base_url="http://app:9000",
    )
    assert len(rows) == 1
    assert rows[0]["samples"] == 3


# ---------- OpenAPI ----------

def test_parse_openapi_v3():
    spec = {
        "openapi": "3.0.0",
        "servers": [{"url": "http://demo-app:9000"}],
        "paths": {
            "/api/users": {
                "get": {"summary": "List users", "responses": {"200": {"description": "ok"}}}
            },
            "/api/checkout": {
                "post": {
                    "summary": "Checkout",
                    "security": [{"bearer": []}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"500": {"description": "server error"}},
                }
            },
        },
    }
    rows = parse_openapi(spec)
    assert len(rows) == 2
    users = next(r for r in rows if r["path"] == "/api/users")
    checkout = next(r for r in rows if r["path"] == "/api/checkout")
    assert users["method"] == "GET"
    assert users["group"] == "users"
    assert checkout["auth_required"] is True
    assert checkout["group"] == "orders"


def test_parse_openapi_swagger2_basepath():
    spec = {"swagger": "2.0", "basePath": "/api", "paths": {"/orders": {"get": {"responses": {}}}}}
    rows = parse_openapi(spec)
    assert rows[0]["path"] == "/api/orders"


# ---------- architecture map ----------

def test_build_architecture_map():
    frontend = {"frameworks": ["static-html"], "css": [], "evidence": {}}
    arch = build_architecture_map(frontend, 4, "http://demo-app:9000")
    assert arch["layers"][1]["detail"] == "static-html"
    assert "4 endpoints" in arch["layers"][2]["detail"]
