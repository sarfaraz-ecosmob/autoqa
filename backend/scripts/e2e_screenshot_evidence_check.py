"""E2E check: the run page evidence viewer really shows a captured screenshot.

Logs in through the UI, opens the most recent browser run of the Parabank
project, clicks an execution row and asserts the <img> decoded real pixels
(not the "No screenshot captured" empty state).

Run inside the browser-worker container:
    docker compose exec -T browser-worker python /app/scripts/e2e_screenshot_evidence_check.py
"""
import sys

from playwright.sync_api import sync_playwright

BASE = "http://nginx"
EMAIL = "live@example.com"
PASSWORD = "S3curePass!xyz"
PROJECT = "9b196a4861bb44a0906d9437fbd4da04"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # login through the UI (fresh token, like a real user)
        page.goto(f"{BASE}/login", wait_until="networkidle")
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(f"{BASE}/", timeout=15000)

        api = page.request
        tok = page.evaluate("() => localStorage.getItem('autoqa_token')")
        hdr = {"Authorization": f"Bearer {tok}"}

        # newest run of the project
        runs = api.get(f"{BASE}/api/projects/{PROJECT}/test-runs", headers=hdr).json()
        run = next(r for r in runs if r["label"] == "screenshot-e2e")
        execs = api.get(
            f"{BASE}/api/projects/{PROJECT}/test-runs/{run['id']}/executions", headers=hdr
        ).json()
        assert execs, "run has no executions"
        failed = [e for e in execs if e["status"] == "failed"]
        assert failed, f"expected a failed browser execution, got {execs}"

        # every failed browser execution must expose screenshot evidence
        for e in execs:
            ev = api.get(
                f"{BASE}/api/projects/{PROJECT}/executions/{e['id']}/evidence", headers=hdr
            ).json()
            if e["status"] == "failed":
                assert ev.get("screenshot_base64"), f"{e['ref']} has no screenshot evidence"
            print(f"    {e['ref']}: status={e['status']} "
                  f"shot={'yes' if ev.get('screenshot_base64') else 'no'}")

        # open the run page and view the evidence through the UI
        page.goto(f"{BASE}/projects/{PROJECT}/runs/{run['id']}", wait_until="networkidle")
        rows = page.locator("table tbody tr")
        rows.first.wait_for(timeout=15000)
        rows.first.click()

        img = page.locator("img[alt^='Evidence for']")
        img.wait_for(state="visible", timeout=15000)
        page.wait_for_function(
            "() => { const i = document.querySelector(\"img[alt^='Evidence for']\");"
            " return i && i.naturalWidth > 0 && i.naturalHeight > 0; }",
            timeout=15000,
        )
        size = page.evaluate(
            "() => { const i = document.querySelector(\"img[alt^='Evidence for']\");"
            " return [i.naturalWidth, i.naturalHeight]; }"
        )
        assert size[0] > 0 and size[1] > 0, f"screenshot did not decode: {size}"
        empty_state = page.locator("text=No screenshot captured for this execution.")
        assert empty_state.count() == 0, "evidence modal shows the empty state"

        page.screenshot(path="/tmp/autoqa-evidence-modal.png", full_page=False)
        print(f"[OK] evidence modal rendered screenshot {size[0]}x{size[1]}")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
