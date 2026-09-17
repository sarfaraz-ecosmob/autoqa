"""API test executor (spec §9): GET/POST/PUT/PATCH/DELETE/GraphQL + validations.

Phase 9 additions: variable chaining ({{var}} substitution + extract steps),
environment variables, and response-time capture per request.
"""
import re
import time
from typing import Any

import httpx

from app.security.ssrf import validate_target_url

_VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")


def substitute_vars(value: Any, variables: dict) -> Any:
    """Replace {{name}} placeholders in strings (recursively in dicts/lists)."""
    if isinstance(value, str):
        def repl(match: re.Match) -> str:
            return str(variables.get(match.group(1), match.group(0)))
        return _VAR_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: substitute_vars(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_vars(v, variables) for v in value]
    return value


def extract_path(body: Any, path: str) -> Any:
    """Extract a value from JSON using dotted paths with [index] segments."""
    current = body
    for part in path.replace("]", "").replace("[", ".").split("."):
        if part == "":
            continue
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


class ApiExecutor:
    def __init__(
        self,
        base_url: str,
        variables: dict | None = None,
        timeout_seconds: float = 30.0,
        auth: dict | None = None,
        credentials: dict | None = None,
    ):
        self.base_url = validate_target_url(base_url).rstrip("/")
        self.variables: dict = dict(variables or {})
        self.timeout_seconds = timeout_seconds
        # Authenticated-API support (spec §9): when the project has credentials
        # + auth config, every request gets the auth headers auto-attached.
        self.auth: dict = auth or {}
        self.credentials: dict = credentials or {}
        self.auth_headers: dict = {}
        self._log: list[dict] = []

    def run(self, steps: list[dict]) -> dict:
        """Execute API steps; returns {status, actual_result, error_details, log}."""
        log: list[dict] = []
        last_status: int | None = None
        last_body: Any = None
        last_headers: dict = {}
        last_ms: int | None = None
        error: dict = {}

        if self.auth and self.credentials:
            from app.execution.auth_flow import resolve_api_auth_headers

            self.auth_headers = resolve_api_auth_headers(
                self.auth, self.credentials, self.variables, self.base_url, log
            )
            guard = self._unauthenticated_guard(steps)
            if guard:
                log.extend(guard)

        with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds) as client:
            for step in steps:
                action = step.get("action")
                entry: dict = {"action": action}

                if action == "request":
                    method = str(step.get("method", "GET")).upper()
                    path = substitute_vars(step.get("path", "/"), self.variables)
                    payload = substitute_vars(step.get("json") or step.get("body"), self.variables)
                    headers = substitute_vars(step.get("headers"), self.variables)
                    # Auto-attach auth headers (step-level headers win)
                    merged_headers = {**(self.auth_headers or {}), **(headers or {})}
                    start = time.perf_counter()
                    try:
                        resp = client.request(
                            method,
                            path,
                            json=payload if isinstance(payload, dict) else None,
                            content=payload if isinstance(payload, str) else None,
                            headers=merged_headers or None,
                        )
                    except httpx.HTTPError as exc:
                        log.append({**entry, "result": "error", "error": str(exc)})
                        return {
                            "status": "failed",
                            "actual_result": f"{method} {path} raised {type(exc).__name__}",
                            "error_details": {"type": "exception", "message": str(exc)},
                            "log": log,
                        }
                    elapsed_ms = int((time.perf_counter() - start) * 1000)
                    last_status = resp.status_code
                    last_ms = elapsed_ms
                    last_headers = dict(resp.headers)
                    try:
                        last_body = resp.json()
                    except ValueError:
                        last_body = resp.text[:2000]
                    entry.update({"status": resp.status_code, "ms": elapsed_ms})
                    log.append(entry)

                elif action == "extract":
                    value = extract_path(last_body, step.get("path", ""))
                    var_name = step.get("var", "")
                    if var_name:
                        self.variables[var_name] = value
                    entry["result"] = "pass" if value is not None else "fail"
                    entry["var"] = var_name
                    log.append(entry)
                    if value is None and step.get("required", True):
                        error = {"type": "extract_failed", "path": step.get("path", "")}

                elif action == "expect_status":
                    expected = substitute_vars(step.get("value"), self.variables)
                    ok = self._status_matches(last_status, expected)
                    entry["result"] = "pass" if ok else "fail"
                    entry["expected"] = expected
                    entry["actual"] = last_status
                    log.append(entry)
                    if not ok:
                        error = {"type": "status_mismatch", "expected": expected,
                                 "actual": last_status, "body": last_body}

                elif action == "expect_status_in":
                    allowed = step.get("value") or []
                    if isinstance(allowed, str):
                        allowed = [p.strip() for p in allowed.split(",") if p.strip()]
                    ok = last_status is not None and str(last_status) in {str(v) for v in allowed}
                    entry["result"] = "pass" if ok else "fail"
                    entry["expected"] = allowed
                    entry["actual"] = last_status
                    log.append(entry)
                    if not ok and not error:
                        error = {"type": "status_mismatch", "expected": allowed,
                                 "actual": last_status, "body": last_body}

                elif action == "expect_header_contains":
                    header = str(step.get("header", "")).lower()
                    needle = str(step.get("value", ""))
                    actual = str(last_headers.get(header, ""))
                    ok = needle.lower() in actual.lower()
                    entry["result"] = "pass" if ok else "fail"
                    entry["header"] = header
                    log.append(entry)
                    if not ok and not error:
                        error = {"type": "header_mismatch", "header": header,
                                 "expected": needle, "actual": actual}

                elif action == "expect_response_time_under_ms":
                    limit = int(step.get("value", 3000))
                    ok = last_ms is not None and last_ms <= limit
                    entry["result"] = "pass" if ok else "fail"
                    entry["expected_under_ms"] = limit
                    entry["actual_ms"] = last_ms
                    log.append(entry)
                    if not ok and not error:
                        error = {"type": "slow_response", "limit_ms": limit, "actual_ms": last_ms}

                elif action == "expect_json_contains":
                    needle = step.get("key", "")
                    ok = isinstance(last_body, dict) and needle in json_keys(last_body)
                    entry["result"] = "pass" if ok else "fail"
                    log.append(entry)
                    if not ok and not error:
                        error = {"type": "schema_mismatch", "missing_key": needle}

                elif action == "expect_json_value":
                    path = substitute_vars(step.get("path", ""), self.variables)
                    expected = substitute_vars(step.get("value"), self.variables)
                    actual = extract_path(last_body, path)
                    ok = actual == expected
                    entry["result"] = "pass" if ok else "fail"
                    entry["path"] = path
                    log.append(entry)
                    if not ok and not error:
                        error = {"type": "value_mismatch", "path": path,
                                 "expected": expected, "actual": actual}

                else:
                    log.append({**entry, "result": "skipped", "reason": "unknown action"})

                if error:
                    break

        return {
            "status": "passed" if not error else "failed",
            "actual_result": (
                f"final status {last_status} in {last_ms}ms"
                if last_status is not None
                else "no request executed"
            ),
            "error_details": error,
            "log": log,
            "latency_ms": last_ms,
        }

    def _unauthenticated_guard(self, steps: list[dict]) -> list[dict] | None:
        """Warn (in the log) when the project has credentials but this API case
        runs without the auth headers — helps diagnose auth regressions."""
        if self.auth and self.credentials and not self.auth_headers:
            return [{
                "action": "auth_guard",
                "result": "warn",
                "note": "credentials configured but no auth headers resolved — requests run unauthenticated",
            }]
        return None

    @staticmethod
    def _status_matches(actual: int | None, expected: Any) -> bool:
        if actual is None:
            return False
        if expected == "2xx":
            return 200 <= actual < 300
        if isinstance(expected, str) and expected.endswith("xx"):
            prefix = int(expected[0]) * 100
            return prefix <= actual < prefix + 100
        return actual == expected


def json_keys(obj: Any, prefix: str = "") -> set[str]:
    """Flatten top-2-level JSON keys for contains-checks."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}{k}"
            keys.add(path)
            if isinstance(v, dict):
                keys |= json_keys(v, f"{path}.")
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                keys |= json_keys(v[0], f"{path}[].")
    return keys
