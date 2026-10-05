"""E2E UI check: credentials show/hide + real-browser "Test sign-in" + authenticated scan.

Drives the Overview page like a real user:
  1. login via UI → create a disposable project (http://demo-app:9000)
  2. fill Username / Password / Login URL in the Test credentials card → Save
  3. reload → "Saved configuration" panel must display login_url + selectors
     (password still masked) — the original complaint
  4. "Show values" reveals the decrypted values; "Hide values" masks again
  5. "Test sign-in" runs the real-browser check → expect Sign-in OK
  6. wrong password via API → test-sign-in must FAIL (and tell us why)
  7. run a discovery scan → crawler signs in first and discovers pages
     beyond the login wall
  8. delete the disposable project (204)

Run inside the browser-worker container:
    docker compose exec -T browser-worker python /app/scripts/e2e_ui_credentials_check.py
"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://nginx"
EMAIL = "live@example.com"
PASSWORD = "S3curePass!xyz"
PREFIX = "UI Creds Check"
DEMO_USER = "alice@demo.io"
DEMO_PASS = "S3curePass!"


def main() -> int:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    proj_name = f"{PREFIX} {stamp}"
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # ---------- 1. Login through the UI + create disposable project ----------
        page.goto(f"{BASE}/login", wait_until="networkidle")
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(f"{BASE}/", timeout=10000)

        api = page.request

        def hdr() -> dict:
            tok = page.evaluate("() => localStorage.getItem('autoqa_token')")
            return {"Authorization": f"Bearer {tok}"}

        # cleanup leftovers
        for proj in api.get(f"{BASE}/api/projects", headers=hdr()).json():
            if proj["name"].startswith(PREFIX):
                api.delete(f"{BASE}/api/projects/{proj['id']}", headers=hdr())
                print(f"    cleaned leftover '{proj['name']}'")

        page.click("text=+ New project")
        page.fill("input[type=url]", "http://demo-app:9000")
        page.fill("form input:not([type=url]):not([type=checkbox])", proj_name)
        page.check("input[type=checkbox]")
        page.click("button[type=submit]")
        card = page.locator("a", has_text=proj_name).first
        card.wait_for(timeout=10000)
        card.click()
        page.wait_for_selector("text=Test credentials", timeout=10000)

        pid = next(
            x["id"] for x in api.get(f"{BASE}/api/projects", headers=hdr()).json()
            if x["name"] == proj_name
        )
        print(f"[1] project created + overview open: {proj_name}")

        # ---------- 2. Enter credentials in the UI form and save ----------
        inputs = page.locator("form:has(input[type=password]) input, div.rounded-xl:has(input[type=password]) input")
        # Robust targeting: fill by label proximity
        def field(label: str):
            return page.locator(f"label:has-text('{label}') + input").first

        card_el = page.locator("div.rounded-xl", has=page.get_by_text("Test credentials")).first
        u_input = card_el.locator("input").nth(0)  # username (first input in card)
        # The card order: username, password, login_url, ... — drive via JS-free fill:
        card_el.locator("input").nth(0).fill(DEMO_USER)
        card_el.locator("input[type=password]").first.fill(DEMO_PASS)
        # login URL is the full-width input after the 2-col grid
        card_el.get_by_placeholder("https://staging.example.com/login").fill("http://demo-app:9000/login")
        card_el.get_by_role("button", name="Save credentials").click()
        page.wait_for_selector("text=Saved — credentials are encrypted at rest.", timeout=10000)
        print("[2] credentials saved via UI form")

        # ---------- 3. Reload → saved details must be VISIBLE (original bug) ----------
        page.reload(wait_until="networkidle")
        card_el = page.locator("div.rounded-xl", has=page.get_by_text("Test credentials")).first
        card_el.wait_for(timeout=10000)
        assert card_el.get_by_text("Saved configuration").count() == 1, (
            "saved-configuration panel missing after reload"
        )
        panel_text = card_el.inner_text()
        assert "http://demo-app:9000/login" in panel_text, f"login URL not visible in saved config: {panel_text}"
        assert "input[type='password']" in panel_text, "password selector masked/dropped"
        assert "qa@x.com" not in panel_text  # sanity: not leaked from another fixture
        print("[3] saved config visible after reload (login URL + selectors) ✅")

        # ---------- 4. Show values → decrypted; Hide → masked ----------
        card_el.get_by_role("button", name="Show values").click()
        page.wait_for_timeout(800)
        shown_text = card_el.inner_text()
        assert DEMO_PASS in shown_text, "reveal did not show the password"
        assert DEMO_USER in shown_text, "reveal did not show the username"
        card_el.get_by_role("button", name="Hide values").click()
        page.wait_for_timeout(300)
        hidden_text = card_el.inner_text()
        assert DEMO_PASS not in hidden_text, "password still visible after Hide"
        print("[4] show/hide toggle works ✅")

        # ---------- 4b. Auto-detect selectors from the demo-app login page ----------
        card_el = page.locator("div.rounded-xl", has=page.get_by_text("Test credentials")).first
        # clear the saved login URL field to simulate typing it fresh
        card_el.get_by_placeholder("https://staging.example.com/login").fill("")
        card_el.get_by_placeholder("https://staging.example.com/login").fill("http://demo-app:9000/login")
        card_el.get_by_role("button", name="Auto-detect").click()
        detected = False
        for _ in range(40):
            page.wait_for_timeout(1500)
            body = card_el.inner_text()
            if "Detected selectors" in body or ("failed" in body.lower() and "Detecting" not in body):
                detected = "Detected selectors" in body
                break
        assert detected, f"auto-detect did not report success; card text: {card_el.inner_text()[:400]}"
        print("[4b] Auto-detect filled the selector fields ✅")

        # ---------- 4c. Dry run with the detected selectors → expect success ----------
        # The form still has the saved username/password? They were cleared after
        # save — refill from revealed values to mimic a real user re-testing.
        card_el.get_by_role("button", name="Show values").click()
        page.wait_for_timeout(800)
        # re-hide to keep state clean
        card_el.get_by_role("button", name="Hide values").click()
        page.wait_for_timeout(300)
        card_el.get_by_role("button", name="Dry run login").click()
        dry_ok = False
        for _ in range(60):
            page.wait_for_timeout(2000)
            body = card_el.inner_text()
            if "Dry run successful" in body or ("Dry run FAILED" in body):
                dry_ok = "Dry run successful" in body
                break
        assert dry_ok, f"dry run did not pass; card text: {card_el.inner_text()[:400]}"
        print("[4c] Dry run login: 'Dry run successful — signed in' message shown ✅")

        # ---------- 5. Test sign-in (real browser task) → expect OK ----------
        card_el.get_by_role("button", name="Test sign-in").click()
        ok_msg = False
        for _ in range(60):
            page.wait_for_timeout(2000)
            body = card_el.inner_text()
            if "Signed in" in body:
                ok_msg = True
                break
            if "FAILED" in body and "launching" not in body:
                break
        assert ok_msg, f"test sign-in did not pass; card text: {card_el.inner_text()[:500]}"
        print("[5] Test sign-in button: PASSED with final URL ✅")

        # ---------- 6. Wrong password must fail with a clear message ----------
        # demo-app accepts any non-empty password; the literal "wrong" triggers
        # its deliberate server error → login JS shows the alert, stays on /login.
        r = api.patch(f"{BASE}/api/projects/{pid}", headers=hdr(),
                      data={"credentials": {"username": DEMO_USER, "password": "wrong"}})
        assert r.ok, f"patch wrong password failed: {r.status}"
        r = api.post(f"{BASE}/api/projects/{pid}/test-sign-in", headers=hdr())
        assert r.status == 202, f"test-sign-in start failed: {r.status} {r.text()[:200]}"
        task_id = r.json()["task_id"]
        result = {}
        for _ in range(40):
            s = api.get(f"{BASE}/api/projects/{pid}/test-sign-in/status",
                        params={"task_id": task_id}, headers=hdr()).json()
            if s.get("status") == "SUCCESS":
                result = s.get("result") or {}
                break
            time.sleep(1.5)
        assert result.get("login_ok") is False, f"wrong password unexpectedly passed: {result}"
        print(f"[6] wrong password → login_ok=false (assertion: {result.get('log', [{}])[-1].get('assert', 'n/a')}) ✅")

        # ---------- 7. Restore good creds + run a scan: crawler signs in first ----------
        r = api.patch(f"{BASE}/api/projects/{pid}", headers=hdr(),
                      data={"credentials": {"username": DEMO_USER, "password": DEMO_PASS}})
        assert r.ok
        r = api.post(f"{BASE}/api/projects/{pid}/scan", headers=hdr(),
                     data={"max_depth": 1, "max_urls": 20})
        assert r.status == 202, f"scan start failed: {r.status} {r.text()[:200]}"
        scan_id = r.json()["scan_id"]
        state = None
        for _ in range(60):
            s = api.get(f"{BASE}/api/projects/{pid}/scan/status",
                        params={"scan_id": scan_id}, headers=hdr()).json()
            state = s.get("status")
            if state in ("SUCCESS", "FAILURE", "completed"):
                break
            time.sleep(2)
        assert state == "SUCCESS", f"scan did not complete: {state}"

        pages = api.get(f"{BASE}/api/projects/{pid}/scan/pages", headers=hdr()).json()
        login_rows = [pg for pg in pages if "auth" in pg.get("components", {}) or pg["url"].endswith("/login")]
        dashboard = [pg for pg in pages if "/dashboard" in pg["url"]]
        assert len(pages) >= 2, f"expected >1 discovered page, got {len(pages)}: {[pg['url'] for pg in pages]}"
        assert dashboard, "dashboard (behind login) was NOT discovered — crawler login may have failed"
        print(f"[7] authenticated scan: {len(pages)} pages incl. dashboard behind the login wall ✅")

        # ---------- 7b. API auth dry run (sync endpoint) ----------
        r = api.post(f"{BASE}/api/projects/{pid}/test-api-auth", headers=hdr(),
                     data={"api_auth_header": "Authorization", "api_auth_prefix": "Bearer ",
                           "api_auth_header_value": "demo-token"})
        assert r.status == 200, f"test-api-auth failed: {r.status} {r.text()[:200]}"
        body = r.json()
        assert body["ok"] is True and "Dry run successful" in body["message"], f"api auth dry run: {body}"
        assert "demo-token" not in body["header"]["value_masked"], "token leaked in response!"
        print(f"[7b] API auth dry run: {body['message'][:90]}… (token masked) ✅")

        # ---------- 8. Cleanup: delete the disposable project ----------
        r = api.delete(f"{BASE}/api/projects/{pid}", headers=hdr())
        assert r.status == 204, f"cleanup delete failed: {r.status}"
        print("[8] disposable project deleted (204) — CREDENTIALS E2E PASSED ✅")

        page.screenshot(path="/tmp/autoqa-ui-creds.png", full_page=True)
        browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
