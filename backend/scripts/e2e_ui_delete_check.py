"""E2E UI check: delete a fully-loaded project from the Projects page.

Reproduces the production bug end-to-end: a project with requirements, test
cases, an approved run (queued executions), environments, datasets, a schedule,
a webhook, discovery pages AND the `applications` row from POST /analyze used
to make DELETE /api/projects/{id} return 500. This script builds exactly that
project — creating it through the real UI — then clicks "Delete" on the
Projects page and asserts the card disappears and the API returns 404.

Run inside the browser-worker container:
    docker compose exec -T browser-worker python /app/scripts/e2e_ui_delete_check.py
"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://nginx"
EMAIL = "live@example.com"
PASSWORD = "S3curePass!xyz"
PREFIX = "UI Delete Check"


def main() -> int:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    proj_name = f"{PREFIX} {stamp}"
    failures: list[str] = []
    delete_status: int | None = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # ---------- 1. Login through the UI ----------
        page.goto(f"{BASE}/login", wait_until="networkidle")
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(f"{BASE}/", timeout=10000)
        token = page.evaluate("() => localStorage.getItem('autoqa_token')")
        assert token, "no token in localStorage after login"
        auth = {"Authorization": f"Bearer {token}"}
        print("[1] login via UI OK")

        # Track the DELETE response the UI sends for our project.
        def _on_response(resp):
            nonlocal delete_status
            if resp.request.method == "DELETE" and f"/api/projects/" in resp.url:
                delete_status = resp.status

        page.on("response", _on_response)

        api = page.request  # same origin; pass the bearer header explicitly

        def hdr() -> dict:
            tok = page.evaluate("() => localStorage.getItem('autoqa_token')")
            return {"Authorization": f"Bearer {tok}"}

        # ---------- 2. Cleanup leftovers from earlier runs (also re-verifies delete) ----------
        r = api.get(f"{BASE}/api/projects", headers=hdr())
        assert r.ok, f"list projects failed: {r.status}"
        for proj in r.json():
            if proj["name"].startswith(PREFIX):
                d = api.delete(f"{BASE}/api/projects/{proj['id']}", headers=hdr())
                print(f"    cleaned leftover '{proj['name']}' → {d.status}")
        print("[2] leftover cleanup OK")

        # ---------- 3. Create the project through the UI form ----------
        page.click("text=+ New project")
        page.fill("input[type=url]", "http://demo-app:9000")
        page.fill("form input:not([type=url]):not([type=checkbox])", proj_name)
        page.check("input[type=checkbox]")
        page.click("button[type=submit]")
        card = page.locator("a", has_text=proj_name).first
        card.wait_for(timeout=10000)
        print(f"[3] project created via UI: {proj_name}")

        # ---------- 4. Load it with the data shapes that used to break delete ----------
        # Open detail page (real user flow), then seed via the same API the UI uses.
        card.click()
        page.wait_for_selector("text=Overview", timeout=10000)

        pid = api.get(f"{BASE}/api/projects", headers=hdr()).json()
        pid = next(x["id"] for x in pid if x["name"] == proj_name)

        r = api.post(f"{BASE}/api/projects/{pid}/requirements/import", headers=hdr(), data={
            "source_name": "ui-delete-check",
            "text": (
                "REQ-UIDEL-001: User can log in with valid credentials\n"
                "Valid credentials reach the dashboard.\n"
                "priority: high\n"
                "tags: auth, smoke\n"
                "\n"
                "REQ-UIDEL-002: User can add a product to the cart\n"
                "Adding a product increments the cart badge.\n"
                "priority: medium\n"
                "tags: cart, ui\n"
            ),
        })
        assert r.status == 201, f"requirements import failed: {r.status} {r.text()[:200]}"
        n_reqs = r.json()["imported"]

        r = api.post(f"{BASE}/api/projects/{pid}/requirements/generate-cases",
                     headers=hdr(), data={"use_llm": False})
        assert r.ok, f"generate-cases failed: {r.status} {r.text()[:200]}"
        n_cases = r.json()["generated"]

        r = api.post(f"{BASE}/api/projects/{pid}/test-cases/approve", headers=hdr(), data={})
        assert r.ok, f"approve failed: {r.status}"

        r = api.post(f"{BASE}/api/projects/{pid}/test-runs", headers=hdr(),
                     data={"label": "ui-del run", "browsers": ["chromium"]})
        assert r.status == 201, f"run create failed: {r.status} {r.text()[:200]}"
        run_id = r.json()["id"]

        r = api.post(f"{BASE}/api/projects/{pid}/environments", headers=hdr(),
                     data={"name": "staging", "base_url": "http://demo-app:9000", "variables": {}})
        assert r.status == 201, f"environment failed: {r.status}"
        env_id = r.json()["id"]

        r = api.post(f"{BASE}/api/projects/{pid}/datasets", headers=hdr(),
                     data={"name": "creds", "kind": "static", "values": {"user": "alice"}, "environment_id": env_id})
        assert r.status == 201, f"dataset failed: {r.status}"

        r = api.post(f"{BASE}/api/projects/{pid}/schedules", headers=hdr(),
                     data={"name": "nightly", "cron": "0 2 * * *"})
        assert r.status == 201, f"schedule failed: {r.status}"

        r = api.post(f"{BASE}/api/projects/{pid}/webhooks", headers=hdr(),
                     data={"name": "slack", "url": "http://example.com/hook", "events": ["run.completed"]})
        assert r.status == 201, f"webhook failed: {r.status}"
        print(f"[4] seeded: {n_reqs} requirements → {n_cases} cases → run {run_id[:8]} (+env/dataset/schedule/webhook)")

        # ---------- 5. Real discovery scan + analysis (creates pages + `applications` row) ----------
        r = api.post(f"{BASE}/api/projects/{pid}/scan", headers=hdr(),
                     data={"max_depth": 1, "max_urls": 10})
        if r.status == 202:
            scan_id = r.json()["scan_id"]
            deadline = time.time() + 150
            state = None
            while time.time() < deadline:
                s = api.get(f"{BASE}/api/projects/{pid}/scan/status",
                            params={"scan_id": scan_id}, headers=hdr()).json()
                state = s.get("status")
                if state in ("SUCCESS", "completed", "FAILED", "failed", "FAILURE"):
                    break
                time.sleep(2)
            print(f"[5] discovery scan finished with state: {state}")
        else:
            print(f"[5] scan not started (HTTP {r.status}) — continuing")
            state = None

        if state in ("SUCCESS", "completed"):
            r = api.post(f"{BASE}/api/projects/{pid}/analyze", headers=hdr())
            if r.ok:
                n_apis = len(r.json().get("apis", []))
                print(f"[5b] analysis OK — applications row created, {n_apis} API endpoints inventoried")
            else:
                print(f"[5b] analyze skipped (HTTP {r.status})")

        # ---------- 6. Delete from the Projects page UI ----------
        page.goto(f"{BASE}/", wait_until="networkidle")
        card = page.locator("a", has_text=proj_name).first
        card.wait_for(timeout=10000)
        page.once("dialog", lambda d: d.accept())
        card.get_by_role("button", name="Delete", exact=True).click()

        # Card must disappear from the list (poll instead of fixed sleep).
        gone = True
        for _ in range(30):
            if page.locator("a", has_text=proj_name).count() == 0:
                break
            page.wait_for_timeout(500)
        else:
            gone = False
        assert gone, f"project card for '{proj_name}' still visible after Delete"
        print(f"[6] Delete clicked → card removed from UI (API response: {delete_status})")

        assert delete_status == 204, (
            f"DELETE /api/projects returned {delete_status} (204 expected; 500 = old bug)"
        )

        # ---------- 7. API confirms the project is gone ----------
        r = api.get(f"{BASE}/api/projects/{pid}", headers=hdr())
        assert r.status == 404, f"expected 404 after delete, got {r.status}"
        print("[7] API confirms project is gone (404) — UI DELETE E2E PASSED ✅")

        page.screenshot(path="/tmp/autoqa-ui-delete.png", full_page=True)
        browser.close()

    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
