"""API test executor (spec §9): GET/POST/PUT/PATCH/DELETE/GraphQL + validations."""
import time
from typing import Any

import httpx

from app.security.ssrf import validate_target_url


class ApiExecutor:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0):
        self.base_url = validate_target_url(base_url).rstrip("/")

    def run(self, steps: list[dict]) -> dict:
        """Execute API steps; returns {status, actual_result, error_details, log}."""
        log: list[dict] = []
        last_status: int | None = None
        last_body: Any = None
        last_ms: int | None = None
        error: dict = {}

        with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds_value()) as client:
            for step in steps:
                action = step.get("action")
                entry: dict = {"action": action}

                if action == "request":
                    method = str(step.get("method", "GET")).upper()
                    path = step.get("path", "/")
                    payload = step.get("json") or step.get("body")
                    start = time.perf_counter()
                    try:
                        resp = client.request(
                            method, path, json=payload if isinstance(payload, dict) else None,
                            content=payload if isinstance(payload, str) else None,
                        )
                    except httpx.HTTPError as exc:
                        entry["result"] = "error"
                        log.append({**entry, "error": str(exc)})
                        return {
                            "status": "failed",
                            "actual_result": f"{method} {path} raised {type(exc).__name__}",
                            "error_details": {"exception": str(exc), "step": entry},
                            "log": log,
                        }
                    elapsed_ms = int((time.perf_counter() - start) * 1000)
                    last_status = resp.status_code
                    last_ms = elapsed_ms
                    try:
                        last_body = resp.json()
                    except ValueError:
                        last_body = resp.text[:2000]
                    entry.update({"status": resp.status_code, "ms": elapsed_ms})
                    log.append(entry)

                elif action == "expect_status":
                    expected = step.get("value")
                    ok = self._status_matches(last_status, expected)
                    entry["result"] = "pass" if ok else "fail"
                    entry["expected"] = expected
                    entry["actual"] = last_status
                    log.append(entry)
                    if not ok:
                        error = {
                            "type": "status_mismatch",
                            "expected": expected,
                            "actual": last_status,
                            "body": last_body,
                        }

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

                else:
                    log.append({**entry, "result": "skipped", "reason": "unknown action"})

                if error:
                    break

        status = "passed" if not error else "failed"
        return {
            "status": status,
            "actual_result": (
                f"final status {last_status} in {last_ms}ms"
                if last_status is not None
                else "no request executed"
            ),
            "error_details": error,
            "log": log,
        }

    @staticmethod
    def timeout_seconds_value() -> float:
        return 30.0

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
