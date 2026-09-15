"""Security scanning (spec §13) — tiered and non-destructive by design.

Tiers:
  passive      → headers, cookies, CORS, information exposure (no probes)
  safe_active  → passive + BENIGN probes only (reflection markers, malformed
                 inputs). No destructive payloads, no DoS, no exploit code.
  full         → safe_active + deeper probes; requires explicit authorization
                 confirmation at request time (spec §13 "Authorized Full Scan").

Every finding carries evidence and remediation guidance. Findings are
*indicators* — severities reflect confidence, not certainty.
"""
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from app.security.ssrf import validate_target_url

_SECURITY_HEADERS: list[tuple[str, str, str]] = [
    # (header, severity_if_missing, remediation)
    ("content-security-policy", "medium", "Add a Content-Security-Policy to mitigate XSS."),
    ("strict-transport-security", "low", "Enable HSTS (HTTPS-only deployments)."),
    ("x-content-type-options", "low", "Send X-Content-Type-Options: nosniff."),
    ("x-frame-options", "low", "Send X-Frame-Options: DENY/SAMEORIGIN (or CSP frame-ancestors)."),
    ("referrer-policy", "low", "Send a Referrer-Policy header (e.g. strict-origin-when-cross-origin)."),
]

_SQL_ERROR_SIGNATURES = (
    "sql syntax", "sqlite3", "psql:", "mysql_fetch", "ora-00933", "odbc",
    "unterminated quoted string", "syntax error at or near",
)

_REFLECTION_MARKER = "qa7zsafe"


@dataclass
class Finding:
    title: str
    severity: str
    category: str
    evidence: dict
    remediation: str

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "severity": self.severity,
            "category": self.category,
            "evidence": self.evidence,
            "remediation": self.remediation,
        }


@dataclass
class ScanReport:
    tier: str
    target: str
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0

    def as_dict(self) -> dict:
        return {
            "tier": self.tier,
            "target": self.target,
            "checked": self.checked,
            "findings": [f.as_dict() for f in self.findings],
            "counts": self.counts(),
        }

    def counts(self) -> dict:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts


def _client(timeout: float = 8.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=False)


def passive_checks(client: httpx.Client, url: str, report: ScanReport) -> None:
    """Header/cookie/disclosure checks — zero side effects."""
    try:
        resp = client.get(url)
    except httpx.HTTPError:
        return
    report.checked += 1
    headers = {k.lower(): v for k, v in resp.headers.items()}

    for header, severity, remediation in _SECURITY_HEADERS:
        if header not in headers:
            report.findings.append(
                Finding(
                    title=f"Missing security header: {header}",
                    severity=severity,
                    category="security_headers",
                    evidence={"url": url, "missing": header, "response_status": resp.status_code},
                    remediation=remediation,
                )
            )

    for cookie in resp.headers.get_list("set-cookie"):
        low = cookie.lower()
        issues = []
        if "httponly" not in low:
            issues.append("HttpOnly missing")
        if "secure" not in low:
            issues.append("Secure missing")
        if "samesite" not in low:
            issues.append("SameSite missing")
        if issues:
            report.findings.append(
                Finding(
                    title="Cookie missing security attributes",
                    severity="medium",
                    category="cookie_security",
                    evidence={"url": url, "cookie": cookie.split(";")[0], "issues": issues},
                    remediation="Set HttpOnly; Secure; SameSite=Lax|Strict on session cookies.",
                )
            )

    server = headers.get("server")
    if server and any(c.isdigit() for c in server):
        report.findings.append(
            Finding(
                title="Server version disclosure",
                severity="low",
                category="information_exposure",
                evidence={"url": url, "server": server},
                remediation="Suppress version banners (Server/X-Powered-By).",
            )
        )


def cors_check(client: httpx.Client, api_url: str, report: ScanReport) -> None:
    """Check for credentialed/wildcard CORS on API endpoints."""
    try:
        resp = client.get(api_url, headers={"Origin": "https://evil.example"})
    except httpx.HTTPError:
        return
    report.checked += 1
    acao = resp.headers.get("access-control-allow-origin")
    if acao == "https://evil.example":
        report.findings.append(
            Finding(
                title="CORS reflects arbitrary Origin",
                severity="high",
                category="cors",
                evidence={"url": api_url, "reflected_origin": acao},
                remediation="Allowlist trusted origins; never reflect arbitrary Origins.",
            )
        )
    elif acao == "*" and resp.headers.get("access-control-allow-credentials") == "true":
        report.findings.append(
            Finding(
                title="CORS wildcard with credentials",
                severity="high",
                category="cors",
                evidence={"url": api_url},
                remediation="Wildcard origins cannot be combined with credentials.",
            )
        )


def reflection_check(client: httpx.Client, url: str, report: ScanReport) -> None:
    """Benign reflection probe: send a harmless marker, check for unescaped echo.

    No scripts are injected or executed — this only detects raw reflection.
    """
    try:
        resp = client.get(url, params={"q": _REFLECTION_MARKER})
    except httpx.HTTPError:
        return
    report.checked += 1
    if _REFLECTION_MARKER in resp.text:
        report.findings.append(
            Finding(
                title="User input reflected without encoding (potential XSS vector)",
                severity="medium",
                category="xss_indicator",
                evidence={
                    "url": url,
                    "probe": _REFLECTION_MARKER,
                    "note": "Benign marker reflected raw — verify output encoding.",
                },
                remediation="HTML-encode user input on output; add a Content-Security-Policy.",
            )
        )


def sqli_error_check(client: httpx.Client, url: str, report: ScanReport) -> None:
    """Error-based SQLi *indicator*: malformed benign input, look for SQL errors.

    Sends only a single quote — no payloads, no data extraction attempts.
    """
    try:
        resp = client.get(url, params={"q": "'"})
    except httpx.HTTPError:
        return
    report.checked += 1
    body = resp.text[:5000].lower()
    hits = [sig for sig in _SQL_ERROR_SIGNATURES if sig in body]
    if hits:
        report.findings.append(
            Finding(
                title="SQL error message exposed on malformed input",
                severity="high",
                category="sqli_indicator",
                evidence={"url": url, "signatures": hits},
                remediation="Use parameterized queries; return generic errors to clients.",
            )
        )


def error_disclosure_check(client: httpx.Client, base_url: str, report: ScanReport) -> None:
    """Forced error: check that stack traces are not returned to clients."""
    probe_url = urljoin(base_url, "/api/orders/not-a-number")
    try:
        resp = client.get(probe_url)
    except httpx.HTTPError:
        return
    report.checked += 1
    body = resp.text[:5000]
    if resp.status_code >= 500 or "traceback" in body.lower() or "exception" in body.lower():
        report.findings.append(
            Finding(
                title="Internal error details exposed to clients",
                severity="medium",
                category="error_handling",
                evidence={"url": probe_url, "status": resp.status_code, "excerpt": body[:200]},
                remediation="Return generic error bodies; log details server-side only.",
            )
        )


def run_scan(base_url: str, tier: str, page_paths: list[str] | None = None) -> ScanReport:
    base_url = validate_target_url(base_url).rstrip("/")
    report = ScanReport(tier=tier, target=base_url)

    with _client() as client:
        passive_checks(client, base_url, report)
        cors_check(client, f"{base_url}/api/users", report)

        if tier in ("safe_active", "full"):
            reflection_check(client, f"{base_url}/search", report)
            sqli_error_check(client, f"{base_url}/search", report)
            error_disclosure_check(client, base_url, report)
            for path in (page_paths or [])[:10]:
                if path.rstrip("/") == base_url.rstrip("/"):
                    continue
                passive_checks(client, path, report)

    return report
