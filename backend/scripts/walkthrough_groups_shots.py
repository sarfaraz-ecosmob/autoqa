"""Capture screenshots of the new Groups test-type + in-browser report features.

Run INSIDE the browser-worker container (has Chromium + the compose network):

    docker compose exec -T browser-worker python - < backend/scripts/walkthrough_groups_shots.py

Writes PNGs to /tmp/wt/ inside the container; copy out with:
    docker compose cp browser-worker:/tmp/wt/06b-groups.png ./docs/img/
    docker compose cp browser-worker:/tmp/wt/08c-executions-filters.png ./docs/img/
    docker compose cp browser-worker:/tmp/wt/12b-report-viewer.png ./docs/img/

Screenshots captured (against the existing "Demo scan" project, seeded data):
  06b-groups.png            Groups tab with the test-type picker + saved group badges
  08c-executions-filters.png Executions tab with the Type-of-test filter + per-run View report
  12b-report-viewer.png     Inline in-browser report preview (HTML report)
"""
import json
import os
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


def shot(page, name: str, full: bool = True) -> None:
    page.wait_for_timeout(600)  # let data settle
    page.screenshot(path=f"{OUT}/{name}.png", full_page=full)
    print(f"  shot: {name}.png")


def main() -> int:
    tok = api_call("POST", "/auth/login", body={"email": EMAIL, "password": PASSWORD})["access_token"]
    projects = api_call("GET", "/projects", tok)
    projects = projects if isinstance(projects, list) else projects.get("items", [])
    proj = next((p for p in projects if p["name"] == "Demo scan"), None)
    if proj is None:
        print("Demo scan project not found")
        return 1
    pid = proj["id"]
    print(f"[seed] project {pid}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.goto(f"{BASE}/login", wait_until="networkidle")
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(f"{BASE}/", timeout=10000)

        hdr = {"Authorization": f"Bearer {tok}"}

        # ---------- Groups tab: pick types, save a demo group ----------
        page.goto(f"{BASE}/projects/{pid}", wait_until="networkidle")
        page.get_by_role("button").filter(has_text="Groups").first.click()
        page.wait_for_selector("text=Saved groups", timeout=15000)
        page.wait_for_selector("text=Type of test to run on this group", timeout=15000)

        cases = api_call("GET", f"/projects/{pid}/test-cases", tok)
        boxes = page.locator("label:has(input[type=checkbox]) input[type=checkbox]")
        if boxes.count() >= 2:
            boxes.nth(0).check()
            boxes.nth(1).check()

        # clean up any leftover demo group from a previous capture
        for s in api_call("GET", f"/projects/{pid}/suites", tok):
            if s["name"] == "Checkout security checks":
                api_call("DELETE", f"/projects/{pid}/suites/{s['id']}", tok)

        page.fill("input[placeholder='New group name…']", "Checkout security checks")
        page.click("button:has-text('Security / authentication')")
        page.click("button:has-text('Functional')")
        page.wait_for_timeout(400)
        page.click("button:has-text('Save selection as group')")
        page.wait_for_selector("text=Checkout security checks", timeout=15000)
        page.wait_for_timeout(500)
        shot(page, "06b-groups")

        # ---------- Executions tab: type filter + per-run View report ----------
        page.get_by_role("button").filter(has_text="Executions").first.click()
        page.wait_for_selector("text=Group to run", timeout=15000)
        page.wait_for_selector("text=Type of test", timeout=15000)
        page.wait_for_timeout(800)  # runs list
        shot(page, "08c-executions-filters")

        # ---------- Reports tab: inline viewer ----------
        reports = api_call("GET", f"/projects/{pid}/reports", tok)
        html_done = [r for r in reports if r["format"] == "html" and r["status"] == "completed"]
        if not html_done:
            created = api_call("POST", f"/projects/{pid}/reports", tok,
                               {"format": "html", "test_run_id": None})
            for _ in range(80):
                row = api_call("GET", f"/projects/{pid}/reports/{created['report_id']}", tok)
                if row.get("status") == "completed":
                    break
                time.sleep(1.5)

        page.get_by_role("button").filter(has_text="Reports").first.click()
        page.wait_for_selector("text=Generate report", timeout=15000)
        view = page.get_by_role("button", name="View", exact=True).first
        view.wait_for(timeout=15000)
        view.click()
        page.wait_for_selector("text=Report preview", timeout=15000)
        page.wait_for_timeout(1500)
        shot(page, "12b-report-viewer")

        browser.close()
    print("DONE — copy out the three PNGs from /tmp/wt/ in the container")
    return 0


if __name__ == "__main__":
    sys.exit(main())
