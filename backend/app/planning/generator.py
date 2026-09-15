"""Test plan generation (spec §6).

Builds a comprehensive, evidence-based test plan covering all 22 required
sections with priorities and categories. Uses the pluggable LLM when
configured; otherwise produces a deterministic heuristic plan from discovery
data (pages, APIs, components) — never fabricated.
"""
import json

from app.ai import generate_json

ALL_SECTIONS: list[str] = [
    "Functional testing",
    "UI testing",
    "API testing",
    "Integration testing",
    "Authentication testing",
    "Authorization testing",
    "Negative testing",
    "Validation testing",
    "Boundary testing",
    "Error handling",
    "Compatibility testing",
    "Regression testing",
    "Performance smoke testing",
    "Accessibility testing",
    "Security testing",
    "Data validation",
    "Session management",
    "File upload/download testing",
    "Testing scope",
    "Out of scope",
    "Application components",
    "Objective",
]


def _sections_from_evidence(pages: list[dict], apis: list[dict]) -> list[dict]:
    """Deterministic plan sections grounded in actual discovery evidence."""
    page_urls = [p.get("url", "") for p in pages]
    has_login = any("login" in u.lower() for u in page_urls)
    has_uploads = any(p.get("components", {}).get("file_uploads", 0) > 0 for p in pages)
    has_forms = any(p.get("components", {}).get("forms", 0) > 0 for p in pages)
    api_groups = sorted({a.get("group", "general") for a in apis})

    sections: list[dict] = [
        {
            "section": "Objective",
            "content": "Validate the application's functional correctness, UI behavior, API contracts, and error handling through automated end-to-end testing.",
            "priority": "critical",
            "category": "functional",
        },
        {
            "section": "Application components",
            "content": f"{len(pages)} discovered pages; API groups: {', '.join(api_groups) if api_groups else 'none observed yet'}.",
            "priority": "high",
            "category": "functional",
        },
        {
            "section": "Functional testing",
            "content": "Exercise every discovered page and core user flow; verify expected content and navigation integrity.",
            "priority": "critical",
            "category": "functional",
        },
        {
            "section": "UI testing",
            "content": "Verify page titles, element visibility, and component rendering (forms, tables, buttons) across desktop and mobile viewports.",
            "priority": "high",
            "category": "ui",
        },
    ]

    if apis:
        sections.append(
            {
                "section": "API testing",
                "content": f"Validate the {len(apis)} discovered API endpoints: status codes, response schemas, response times.",
                "priority": "critical",
                "category": "api",
            }
        )

    if has_login:
        sections.extend(
            [
                {
                    "section": "Authentication testing",
                    "content": "Verify valid login succeeds, invalid credentials are rejected, and session tokens are issued properly.",
                    "priority": "critical",
                    "category": "security",
                },
                {
                    "section": "Session management",
                    "content": "Verify session persistence across navigation and invalidation on logout.",
                    "priority": "high",
                    "category": "security",
                },
            ]
        )

    if has_forms:
        sections.append(
            {
                "section": "Negative testing",
                "content": "Submit malformed, empty, and oversized inputs to every discovered form; verify graceful error responses.",
                "priority": "high",
                "category": "functional",
            }
        )
        sections.append(
            {
                "section": "Validation testing",
                "content": "Verify field-level validation messages and required-attribute enforcement on all forms.",
                "priority": "medium",
                "category": "functional",
            }
        )

    if has_uploads:
        sections.append(
            {
                "section": "File upload/download testing",
                "content": "Upload valid and invalid file types; verify size limits and error handling.",
                "priority": "high",
                "category": "functional",
            }
        )

    sections.extend(
        [
            {
                "section": "Error handling",
                "content": "Force error paths (404 navigation, invalid API payloads); verify no stack traces or sensitive data leak to users.",
                "priority": "high",
                "category": "functional",
            },
            {
                "section": "Boundary testing",
                "content": "Test input length limits and numeric range edges on key forms and API parameters.",
                "priority": "medium",
                "category": "functional",
            },
            {
                "section": "Compatibility testing",
                "content": "Run core flows across Chromium, Firefox, and WebKit with desktop and mobile viewports.",
                "priority": "medium",
                "category": "compatibility",
            },
            {
                "section": "Regression testing",
                "content": "Re-run the approved suite on every new execution; compare results with previous runs.",
                "priority": "high",
                "category": "regression",
            },
            {
                "section": "Performance smoke testing",
                "content": "Measure page load and API response times; flag pages exceeding agreed thresholds.",
                "priority": "medium",
                "category": "performance",
            },
            {
                "section": "Accessibility testing",
                "content": "Check WCAG AA basics: alt text, labels, heading hierarchy, contrast, keyboard navigation.",
                "priority": "medium",
                "category": "accessibility",
            },
            {
                "section": "Security testing",
                "content": "Passive checks first: security headers, cookie flags, reflected input handling; active scans only after explicit authorization.",
                "priority": "critical",
                "category": "security",
            },
            {
                "section": "Data validation",
                "content": "Verify API responses conform to expected schemas and data types.",
                "priority": "medium",
                "category": "api",
            },
            {
                "section": "Authorization testing",
                "content": "Verify unauthenticated access to protected routes is denied.",
                "priority": "critical",
                "category": "security",
            },
            {
                "section": "Integration testing",
                "content": "Verify frontend-to-backend integration for each observed API call path.",
                "priority": "high",
                "category": "integration",
            },
            {
                "section": "Testing scope",
                "content": f"All pages under {pages[0].get('url', '').rsplit('/', 1)[0] if pages else 'the base URL'} discovered during scanning, plus observed APIs.",
                "priority": "high",
                "category": "functional",
            },
            {
                "section": "Out of scope",
                "content": "Load testing beyond agreed limits, destructive payloads, and third-party external services.",
                "priority": "medium",
                "category": "functional",
            },
        ]
    )
    return sections


def build_test_plan(
    project_name: str,
    base_url: str,
    pages: list[dict],
    apis: list[dict],
    frontend: dict,
) -> dict:
    """Generate the full test plan (heuristic, LLM-refined when configured)."""
    heuristic = {
        "objective": f"Comprehensive QA coverage for {project_name} ({base_url})",
        "sections": _sections_from_evidence(pages, apis),
    }

    llm_context = {
        "project": project_name,
        "base_url": base_url,
        "frontend": frontend.get("frameworks", []),
        "pages": [p.get("url") for p in pages][:30],
        "apis": [{"method": a.get("method"), "path": a.get("path"), "group": a.get("group")} for a in apis][:30],
    }
    llm_plan = generate_json(
        system=(
            "You are a senior QA architect. Produce a test plan as JSON with keys "
            "'objective' (string) and 'sections' (array of {section, content, "
            "priority: critical|high|medium|low, category}). Ground everything in "
            "the provided evidence. Do not invent pages or APIs."
        ),
        user=json.dumps(llm_context),
        fallback=heuristic,
    )
    return llm_plan
