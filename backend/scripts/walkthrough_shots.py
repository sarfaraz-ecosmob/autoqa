"""Capture stage-by-stage UI screenshots for docs/WALKTHROUGH.md.

Run INSIDE the browser-worker container (has Chromium + the compose network):

    docker compose exec -T browser-worker python - < backend/scripts/walkthrough_shots.py

Writes PNGs to /tmp/wt/ inside the container; copy out with:

    docker compose cp browser-worker:/tmp/wt ./docs/img
"""
import json
import os
import re
import sys
import time

import urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://nginx"
EMAIL = "live@example.com"
PASSWORD = "S3curePass!xyz"
OUT = "/tmp/wt"
os.makedirs(OUT, exist_ok=True)

API = "http://backend:8000/api"


def api_call(method: str, path: str, token: str | None = None, body: dict | None = None):
    req = urllib.request.Request(f"{API}{path}", method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


def wait_task(path: str, token: str, seconds: int = 180) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        s = api_call("GET", path, token)
        if s.get("status") in ("SUCCESS", "FAILURE", "completed", "failed"):
            return s
        time.sleep(3)
    return {"status": "timeout"}


def shot(page, name: str, full: bool = True) -> None:
    page.wait_for_timeout(600)  # let charts/data settle
    page.screenshot(path=f"{OUT}/{name}.png", full_page=full)
    print(f"  shot: {name}.png")


def main() -> int:
    # ---------------- seed data via API (so every tab has content) --------
    print("[seed] signing in via API…")
    tok = api_call("POST", "/auth/login", body={"email": EMAIL, "password": PASSWORD})["access_token"]
    projects = api_call("GET", "/projects", tok)
    projects = projects if isinstance(projects, list) else projects.get("items", [])
    proj = next((p for p in projects if p["name"] == "Demo Scan"), None)
    if proj is None:
        print("Demo Scan project not found — run the phase gates first")
        return 1
    pid = proj["id"]
    print(f"[seed] project {pid}")

    # credentials + auth flow (Overview card)
    api_call("PATCH", f"/projects/{pid}", tok, {
        "credentials": {"username": "demo_user", "password": "DemoPass123!"},
        "auth": {
            "login_url": "http://demo-app:9000/login",
            "username_selector": "input[name='username']",
            "password_selector": "input[type='password']",
            "submit_selector": "button[type='submit']",
            "success_assert": "url_not_contains:login",
        },
    })
    print("[seed] credentials saved")

    # environment + datasets (Test Data tab)
    envs = api_call("GET", f"/projects/{pid}/environments", tok)["items"]
    env_id = next((e["id"] for e in envs if e["name"] == "staging"), None)
    if not env_id:
        env_id = api_call("POST", f"/projects/{pid}/environments", tok, {
            "name": "staging", "base_url": "http://demo-app:9000",
            "variables": {"support_email": "support@demo.test"},
        })["id"]
    ds = api_call("GET", f"/projects/{pid}/datasets", tok)["items"]
    names = {d["name"] for d in ds}
    if "demo checkout cards" not in names:
        api_call("POST", f"/projects/{pid}/datasets", tok, {
            "name": "demo checkout cards", "kind": "static",
            "values": {"card_number": "4242424242424242", "cvv": "123", "expiry": "12/29"},
        })
    if "qa users (generated)" not in names:
        api_call("POST", f"/projects/{pid}/datasets", tok, {
            "name": "qa users (generated)", "kind": "generated",
            "generator": "users", "generator_params": {"count": 5},
        })
    print("[seed] environments + datasets ready")

    # a11y audit + smoke perf (Quality tab)
    a = api_call("POST", f"/projects/{pid}/quality/a11y/audit", tok, {})
    wait_task(f"/projects/{pid}/quality/a11y/status?audit_id={a['audit_id']}", tok)
    pf = api_call("POST", f"/projects/{pid}/quality/perf/run", tok, {
        "path": "/", "concurrent_users": 2, "duration_seconds": 8,
        "requests_per_second": 5, "max_response_time_ms": 3000,
    })
    wait_task(f"/projects/{pid}/quality/perf/status?perf_id={pf['perf_id']}", tok)
    print("[seed] quality audits done")

    # schedule (Automations tab)
    scheds = api_call("GET", f"/projects/{pid}/schedules", tok)["items"]
    if not any(s["name"] == "Nightly regression" for s in scheds):
        api_call("POST", f"/projects/{pid}/schedules", tok, {
            "name": "Nightly regression", "cron": "0 2 * * *",
            "browsers": ["chromium"], "parallelism": 2,
        })
    print("[seed] schedule ready")

    # report (Reports tab) — whole-project PDF
    reports = api_call("GET", f"/projects/{pid}/reports", tok)
    items = reports if isinstance(reports, list) else reports.get("items", [])
    if not any(r["status"] == "completed" for r in items):
        rep = api_call("POST", f"/projects/{pid}/reports", tok, {"format": "pdf", "test_run_id": None})
        rid = rep["id"]
        deadline = time.time() + 240
        while time.time() < deadline:
            r = api_call("GET", f"/projects/{pid}/reports/{rid}", tok)
            if r["status"] in ("completed", "failed"):
                break
            time.sleep(4)
        print(f"[seed] report {r['status']}")
    else:
        print("[seed] completed report already present")

    # security scan (passive — findings feed reports)
    sec = api_call("GET", f"/projects/{pid}/security/findings", tok)
    sec_items = sec if isinstance(sec, list) else sec.get("items", [])
    if not sec_items:
        sc = api_call("POST", f"/projects/{pid}/security/scan", tok, {"tier": "passive", "page_paths": []})
        wait_task(f"/projects/{pid}/security/scan/status?scan_id={sc['scan_id']}", tok)
    print("[seed] security findings ready")

    # assistant question (Assistant tab)
    api_call("POST", f"/projects/{pid}/assistant/ask", tok, {"question": "Give me a QA summary of this project"})

    # ---------------- screenshots ----------------------------------------
    print("[shots] launching Chromium…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.goto(f"{BASE}/login", wait_until="networkidle")
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(f"{BASE}/", timeout=15000)
        page.wait_for_selector("text=Demo Scan", timeout=15000)
        shot(page, "00-projects", full=False)

        page.click("text=Demo Scan")
        page.wait_for_selector("text=Test cases", timeout=15000)

        def wait_visible(sel: str, seconds: int = 20) -> bool:
            """Poll until ANY text/selector match is visible (all matches scanned)."""
            if not re.match(r"^(text=|css=|xpath=|//|button|input|select|textarea|\[|\.|#)", sel):
                sel = f"text={sel}"
            deadline = time.time() + seconds
            while time.time() < deadline:
                loc = page.locator(sel)
                n = loc.count()
                for i in range(min(n, 10)):
                    try:
                        if loc.nth(i).is_visible():
                            return True
                    except Exception:
                        pass
                page.wait_for_timeout(400)
            return False

        def goto(tab: str, wait_text: str | None = None):
            # match the tab BUTTON exactly (avoid substring hits in page content)
            btn = page.locator("button", has_text=re.compile(rf"^{re.escape(tab)}$", re.I)).first
            for attempt in range(3):
                btn.click()
                if wait_text is None or wait_visible(wait_text, 6):
                    page.wait_for_timeout(800)
                    return
                print(f"  [goto:{tab}] attempt {attempt + 1}: '{wait_text}' not visible")
                page.wait_for_timeout(1200)
            # last resort: JS click (bypasses overlay/actionability quirks)
            try:
                btn.evaluate("el => el.click()")
                page.wait_for_timeout(1200)
                if not wait_text or wait_visible(wait_text, 4):
                    print(f"  [goto:{tab}] activated via JS click")
                    return
            except Exception:
                pass
            body = page.inner_text("body")[:300].replace("\n", " | ")
            loc = page.locator(wait_text if re.match(r"^(text=|css=|xpath=|//|button|input|select|textarea|\[|\.|#)", wait_text) else f"text={wait_text}") if wait_text else None
            info = []
            if loc:
                for i in range(min(loc.count(), 6)):
                    try:
                        el = loc.nth(i)
                        info.append(el.evaluate(
                            "e => JSON.stringify({tag:e.tagName, txt:e.textContent.trim().slice(0,40),"
                            " visible:e.offsetParent!==null, rect:e.getBoundingClientRect().toJSON(),"
                            " disp:getComputedStyle(e).display, vis:getComputedStyle(e).visibility})"
                        ))
                    except Exception as exc:
                        info.append(f"eval-error:{type(exc).__name__}")
            page.screenshot(path=f"{OUT}/debug-{tab}.png", full_page=False)
            print(f"  [goto:{tab}] DEBUG url={page.url} body={body}")
            for x in info:
                print(f"    match: {x}")
            raise RuntimeError(f"could not activate tab {tab}")

        # 1. Overview + credentials card
        goto("Overview")
        shot(page, "01-overview")
        page.locator("text=Test credentials & sign-in").first.scroll_into_view_if_needed()
        shot(page, "01b-credentials")

        # 2. Requirements & traceability — paste, generate, load matrix
        goto("Requirements", "Import requirements")
        page.fill(
            "textarea",
            "REQ-WALK-001: Password reset via email\n"
            "User can reset password using the registered email. POST /api/reset-password\n\n"
            "REQ-WALK-002: Order summary on dashboard\n"
            "Authenticated users see their last 5 orders after login.",
        )
        page.click("button:has-text('Import text')")
        page.wait_for_timeout(1500)
        # re-generate for any missing coverage (idempotent)
        page.click("button:has-text('Generate cases for all')")
        page.wait_for_timeout(2500)
        shot(page, "02-requirements")
        page.click("button:has-text('Load matrix')")
        page.wait_for_timeout(1500)
        shot(page, "02b-traceability")

        # 3. Discovery — run a real scan
        goto("Discovery", "Start scan")
        page.click("text=Start scan")
        try:
            page.wait_for_selector("text=completed", timeout=120000)
        except Exception:
            pass
        shot(page, "03-discovery")

        # 4. APIs
        goto("APIs")
        shot(page, "04-apis")

        # 5. Test plan
        goto("Test plan")
        shot(page, "05-test-plan")

        # 6. Test cases — approve all
        goto("Test cases")
        try:
            page.wait_for_selector("button:has-text('Approve all')", timeout=8000)
            page.click("button:has-text('Approve all')")
            page.wait_for_timeout(1500)
        except Exception:
            pass
        shot(page, "06-test-cases")

        # 7. Test data — moved here to mirror the lifecycle (after cases, before execution)
        goto("Test data")
        shot(page, "07-test-data")

        # 8. Executions — start a fresh run, capture live, then final
        goto("Executions", "Start run")
        page.click("text=▶ Start run")
        page.wait_for_url("**/runs/**", timeout=30000)
        page.wait_for_timeout(9000)
        shot(page, "08-executions-live")
        page.wait_for_timeout(25000)
        shot(page, "08b-executions-done")

        # the run view navigated us off the project page — go back for the rest
        page.goto(f"{BASE}/projects/{pid}", wait_until="networkidle")
        page.wait_for_selector("text=Test cases", timeout=15000)

        # 9. History — compare the two latest runs
        goto("History")
        selects = page.locator("select")
        if selects.count() >= 2:
            selects.nth(0).select_option(index=1)  # skip placeholder option
            selects.nth(1).select_option(index=2)
            page.click("button:has-text('Compare')")
            page.wait_for_timeout(2000)
        shot(page, "09-history")

        # 10. Quality
        goto("Quality")
        shot(page, "10-quality")

        # 11. Assistant — ask, wait for the grounded answer
        goto("Assistant", "input[placeholder*='Ask about']")  # placeholder attr, not text
        page.fill("input[placeholder*='Ask about']", "Give me a QA summary of this project")
        page.keyboard.press("Enter")
        page.wait_for_timeout(15000)
        shot(page, "11-assistant")

        # 12. Reports
        goto("Reports")
        shot(page, "12-reports")

        # 13. Automations
        goto("Automations")
        shot(page, "13-automations")

        browser.close()
    print("DONE — copy out with: docker compose cp browser-worker:/tmp/wt ./docs/img")
    return 0


if __name__ == "__main__":
    sys.exit(main())
