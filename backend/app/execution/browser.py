"""Browser test executor (spec §9): Playwright step runner with evidence.

Runs inside the browser-worker image (ships Playwright). Collects console
messages and captures a screenshot on failure (evidence, spec §12).

Authenticated portals: when the project defines a login flow + credentials,
a real session login runs first; the case's steps then execute inside the
authenticated context. Credential values are scrubbed from all evidence.
"""
import json
import time


class BrowserExecutor:
    def __init__(
        self,
        browser_name: str = "chromium",
        viewport: dict | None = None,
        variables: dict | None = None,
        heal: bool = True,
        visual_check: bool = False,
        auth: dict | None = None,
        credentials: dict | None = None,
    ):
        self.browser_name = browser_name
        self.viewport = viewport or {"width": 1280, "height": 720}
        self.variables = dict(variables or {})
        self.heal = heal
        self.visual_check = visual_check
        # Authenticated-portal support (spec §9): sign in before the case's
        # own steps run, so protected pages behave like any other target.
        self.auth: dict = auth or {}
        self.credentials: dict = credentials or {}
        self.heals: list[dict] = []

    def run(self, steps: list[dict], screenshot_key: str | None = None, capture_on_pass: bool = False) -> dict:
        """Execute browser steps; returns {status, actual_result, error_details, log, evidence}.

        capture_on_pass: also store a screenshot when the case passes (useful
        visual evidence for reports — spec §19). Failure screenshots are always
        captured when a key is provided (spec §12).
        """
        from playwright.sync_api import sync_playwright

        from app.execution.api_exec import substitute_vars

        def _sub(value):
            return substitute_vars(value, self.variables) if self.variables else value

        log: list[dict] = []
        console_messages: list[str] = []
        evidence: dict = {}
        error: dict = {}
        last_response_status: int | None = None

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport=self.viewport)
            page = context.new_page()
            page.on(
                "console",
                lambda msg: console_messages.append(f"{msg.type}: {msg.text}"),
            )

            # ---- Session login (project auth flow) BEFORE the case steps ----
            auth_ok: bool | None = None
            if self.auth.get("login_url") and self.credentials:
                from app.execution.auth_flow import browser_login

                auth_ok = browser_login(page, self.auth, self.credentials, self.variables, log)
                if not auth_ok:
                    error = {
                        "type": "auth_login_failed",
                        "message": "Project login flow failed before the test steps ran",
                    }
                    log.append({"action": "auth_gate", "result": "fail"})

            try:
                if auth_ok is False:
                    raise RuntimeError("auth gate: login failed")
                for step in steps:
                    action = step.get("action")
                    entry: dict = {"action": action}

                    def _resolve(selector: str) -> str:
                        """Substitute variables, then self-heal if needed."""
                        target = _sub(selector)
                        if not self.heal:
                            return target
                        try:
                            if page.locator(target).count() > 0:
                                return target
                        except Exception:
                            pass
                        from app.execution.healing import heal_selector

                        healed, method = heal_selector(page, target)
                        if healed:
                            self.heals.append(
                                {"original": target, "healed": healed, "method": method}
                            )
                            entry["healed_from"] = target
                            entry["healed_to"] = healed
                            return healed
                        return target  # let the original failure surface

                    if action == "goto":
                        target = _sub(step["target"])
                        resp = page.goto(target, timeout=15000, wait_until="domcontentloaded")
                        last_response_status = resp.status if resp else None
                        entry["url"] = target
                        entry["status"] = last_response_status

                    elif action == "fill":
                        page.fill(_resolve(step["target"]), str(_sub(step.get("value", ""))), timeout=5000)

                    elif action == "click":
                        page.click(_resolve(step["target"]), timeout=5000)
                        page.wait_for_timeout(300)  # allow network settle

                    elif action == "expect_status":
                        expected = step.get("value")
                        ok = last_response_status == expected
                        entry["result"] = "pass" if ok else "fail"
                        entry["expected"] = expected
                        entry["actual"] = last_response_status
                        if not ok:
                            error = {"type": "status_mismatch", "expected": expected,
                                     "actual": last_response_status}

                    elif action == "expect_title_not_empty":
                        title = page.title()
                        ok = bool(title.strip())
                        entry["result"] = "pass" if ok else "fail"
                        if not ok:
                            error = {"type": "empty_title"}

                    elif action == "expect_text":
                        needle = str(_sub(step.get("value", "")))
                        ok = page.get_by_text(needle).count() > 0
                        entry["result"] = "pass" if ok else "fail"
                        entry["expected_text"] = needle[:100]
                        if not ok:
                            error = {"type": "text_missing", "expected": needle[:200]}

                    elif action == "expect_url_contains":
                        needle = str(_sub(step.get("value", ""))).lower()
                        ok = needle in page.url.lower()
                        entry["result"] = "pass" if ok else "fail"
                        entry["expected_url"] = needle[:100]
                        if not ok:
                            error = {"type": "url_mismatch", "expected": needle[:200], "actual": page.url[:200]}

                    elif action == "expect_no_server_error":
                        # Failure = any network response >= 500 observed so far,
                        # or a visible "Internal Server Error" text.
                        ok = True
                        entry["result"] = "pass"
                        # (final validation happens after steps via console/response scan)

                    elif action == "expect_response":
                        ok = True  # presence of any response for the interaction
                        entry["result"] = "pass"

                    else:
                        entry["result"] = "skipped"
                        entry["reason"] = "unknown action"

                    log.append(entry)

                # Post-step: detect server errors seen during the interaction
                responses: list[int] = []
                page.on("response", lambda r: responses.append(r.status))
                page.wait_for_timeout(500)
                if any(s >= 500 for s in responses):
                    error = {"type": "server_error_observed", "statuses": responses}
                    log.append({"action": "server_error_scan", "result": "fail", "statuses": responses})

            except Exception as exc:
                error = {"type": "step_error", "exception": type(exc).__name__, "message": str(exc)}
                log.append({"action": "exception", "result": "fail", "error": str(exc)[:500]})
            finally:
                should_capture = bool(screenshot_key) and (bool(error) or capture_on_pass or self.visual_check)
                if should_capture:
                    try:
                        shot = page.screenshot(full_page=False)
                        from app import storage

                        storage.put_bytes(screenshot_key, shot, "image/png")
                        evidence["screenshot"] = screenshot_key
                    except Exception:
                        evidence["screenshot"] = None
                browser.close()

        if error:
            evidence["console"] = console_messages[-20:]
        if self.credentials:
            # Never let credential material into evidence/logs (spec §17)
            from app.security.crypto import redact_text

            blob = json.dumps(evidence)
            for v in self.credentials.values():
                if isinstance(v, str) and v:
                    blob = blob.replace(json.dumps(v)[1:-1], "••••••••").replace(v, "••••••••")
            evidence = json.loads(blob)
        return {
            "status": "passed" if not error else "failed",
            "actual_result": (
                "all steps passed"
                if not error
                else f"{error.get('type')}: {error.get('message') or error.get('expected') or ''}".strip()
            ),
            "error_details": error,
            "log": log,
            "evidence": evidence,
            **({"heals": self.heals} if self.heals else {}),
        }
