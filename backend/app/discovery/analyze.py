"""Architecture & API discovery (spec §4, §5).

Analyzes crawl evidence: fingerprints the frontend stack (only with real
evidence — no weak-fingerprint claims), groups observed API requests into
functional groups, and builds the architecture map. OpenAPI documents are
imported to enrich the API inventory.
"""
import json
from collections import Counter
from urllib.parse import urlsplit

# ---------- Frontend fingerprinting (evidence-gated, spec §4) ----------

_FRONTEND_SIGNATURES: list[dict] = [
    {"name": "Next.js", "needles": ["__NEXT_DATA__", "/_next/static"]},
    {"name": "Nuxt", "needles": ["__NUXT__", "/_nuxt/"]},
    {"name": "React", "needles": ["data-reactroot", "__REACT_DEVTOOLS", "react-dom"]},
    {"name": "Angular", "needles": ["ng-version", "ng-app", "angular.min.js"]},
    {"name": "Vue", "needles": ["data-v-", "vue.runtime", "__vue__"]},
    {"name": "Svelte", "needles": ["svelte-", "__svelte"]},
    {"name": "WordPress", "needles": ["wp-content", "wp-includes"]},
]

_CSS_SIGNATURES: list[dict] = [
    {"name": "Tailwind CSS", "needles": ["tailwind", "--tw-"]},
    {"name": "Bootstrap", "needles": ["bootstrap.min.css", "class=\"btn btn-"]},
    {"name": "Material", "needles": ["material-icons", "mat-"]},
]


def detect_frontend(page_sources: list[str], script_urls: list[str], css_urls: list[str] | None = None) -> dict:
    """Return only technologies with concrete evidence (counts of needles)."""
    haystack = "\n".join(page_sources).lower() + "\n" + "\n".join(s.lower() for s in script_urls)
    if css_urls:
        haystack += "\n" + "\n".join(s.lower() for s in css_urls)
    stack: dict = {"frameworks": [], "css": [], "evidence": {}}

    for sig in _FRONTEND_SIGNATURES:
        hits = [n for n in sig["needles"] if n in haystack]
        if hits:
            stack["frameworks"].append(sig["name"])
            stack["evidence"][sig["name"]] = hits

    for sig in _CSS_SIGNATURES:
        hits = [n for n in sig["needles"] if n in haystack]
        if hits:
            stack["css"].append(sig["name"])
            stack["evidence"][sig["name"]] = hits

    if not stack["frameworks"]:
        stack["frameworks"] = ["static-html"]  # honest fallback, not a guess
    return stack


# ---------- API grouping (spec §5) ----------

_GROUP_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("authentication", ("login", "logout", "auth", "token", "session", "register", "password")),
    ("users", ("user", "profile", "account")),
    ("products", ("product", "item", "catalog", "inventory")),
    ("orders", ("order", "cart", "checkout", "payment")),
    ("reporting", ("report", "analytics", "stats", "metrics")),
]


def classify_api_group(method: str, url: str) -> str:
    path = urlsplit(url).path.lower()
    for group, keywords in _GROUP_RULES:
        if any(k in path for k in keywords):
            return group
    return "general"


def summarize_requests(requests: list[dict], base_url: str) -> list[dict]:
    """Reduce captured browser requests to a per-endpoint API inventory."""
    base_origin = urlsplit(base_url).netloc.lower() if base_url else ""
    endpoints: dict[tuple, dict] = {}

    api_resource_types = {"xhr", "fetch"}
    for req in requests:
        url = req.get("url", "")
        method = req.get("method", "GET").upper()
        rtype = req.get("resource_type", "")
        if rtype not in api_resource_types:
            continue  # documents/assets are pages, not APIs (spec §5)
        parts = urlsplit(url)
        if parts.netloc.lower() != base_origin:
            continue  # third-party assets
        path = parts.path or "/"
        if path.startswith("/assets") or path.endswith((".js", ".css", ".map", ".png", ".ico", ".svg")):
            continue

        key = (method, path)
        entry = endpoints.setdefault(
            key,
            {
                "method": method,
                "path": path,
                "group": classify_api_group(method, url),
                "samples": 0,
                "query_params": sorted({k for k in parts.query.split("&") if k}) if parts.query else [],
            },
        )
        entry["samples"] += 1
        if req.get("status"):
            entry.setdefault("statuses", Counter())
            entry["statuses"][req["status"]] += 1

    result = []
    for entry in endpoints.values():
        statuses = entry.pop("statuses", None)
        entry["observed_statuses"] = dict(statuses) if statuses else {}
        result.append(entry)

    result.sort(key=lambda e: (e["group"], e["path"], e["method"]))
    return result


# ---------- OpenAPI import (spec §5) ----------

def parse_openapi(spec: dict) -> list[dict]:
    """Convert an OpenAPI/Swagger document into API endpoint rows."""
    endpoints: list[dict] = []
    base_path = ""
    if isinstance(spec.get("servers"), list) and spec["servers"]:
        base_path = urlsplit(spec["servers"][0].get("url", "")).path
    elif isinstance(spec.get("basePath"), str):
        base_path = spec["basePath"]

    http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method.lower() not in http_methods or not isinstance(op, dict):
                continue
            params = [
                p.get("name")
                for p in op.get("parameters", []) + path_item.get("parameters", [])
                if isinstance(p, dict) and p.get("name")
            ]
            body_schema = {}
            rb = op.get("requestBody") or {}
            for content in (rb.get("content") or {}).values():
                body_schema = content.get("schema", {})
                break
            responses = {}
            for code, resp in (op.get("responses") or {}).items():
                responses[str(code)] = resp.get("description", "") if isinstance(resp, dict) else ""

            endpoints.append(
                {
                    "method": method.upper(),
                    "path": f"{base_path}{path}",
                    "group": classify_api_group(method.upper(), path),
                    "source": "openapi",
                    "summary": op.get("summary", ""),
                    "request_schema": body_schema,
                    "response_schema": responses,
                    "query_params": params,
                    "auth_required": bool(op.get("security") or spec.get("security")),
                }
            )
    return endpoints


def build_architecture_map(frontend: dict, api_count: int, base_url: str) -> dict:
    """Spec §4 example: Browser → Frontend → Gateway → APIs → DB → External."""
    return {
        "base_url": base_url,
        "layers": [
            {"layer": "Browser", "detail": "Playwright-crawled DOM + network"},
            {"layer": "Frontend", "detail": ", ".join(frontend["frameworks"])},
            {"layer": "API Gateway / Routes", "detail": f"{api_count} endpoints observed/imported"},
            {"layer": "Backend APIs", "detail": "grouped by function (see API inventory)"},
            {"layer": "Database", "detail": "not directly observable — inferred via API behavior"},
            {"layer": "External Services", "detail": "see external links from crawl"},
        ],
        "frontend": frontend,
    }
