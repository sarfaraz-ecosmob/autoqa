"""E2E UI check: group test types + in-browser report viewing.

Drives the app like a real user:
  1. login via UI → create a disposable project (http://demo-app:9000)
  2. discovery scan → generate test cases → approve all (API for speed)
  3. Groups tab: pick cases, choose test types (functional/security), save
     → group row shows the type badges
  4. Run the group from the Groups tab → run only contains cases whose
     category matches the group's declared types
  5. Executions tab: ad-hoc "Type of test" filter → run respects it
  6. unknown test type via API → 422
  7. Reports tab: generate HTML report → View button opens the in-page
     viewer with real report content; Open in browser opens a new tab
  8. Executions tab: "View report" per completed run → inline viewer
  9. delete the disposable project (204)

Run inside the browser-worker container:
    docker compose exec -T browser-worker python /app/scripts/e2e_ui_groups_reports_check.py
"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://nginx"
EMAIL = "live@example.com"
PASSWORD = "S3curePass!xyz"
PREFIX = "UI Groups Check"


def wait_until(fn, timeout_s=120, interval=2.0, desc="condition"):
    """Poll fn() until truthy; return its value."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {desc}; last={last!r}")


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

        # Authorize the project (required before runs)
        r = api.patch(f"{BASE}/api/projects/{pid}", headers=hdr(),
                      data={"authorization_confirmed": True})
        assert r.ok, f"authorize failed: {r.status}"

        # ---------- 2. Discovery + cases (API for speed) ----------
        r = api.post(f"{BASE}/api/projects/{pid}/scan", headers=hdr(),
                     data={"max_depth": 1, "max_urls": 10})
        assert r.status == 202, f"scan start failed: {r.status} {r.text()[:200]}"
        scan_id = r.json()["scan_id"]
        wait_until(lambda: api.get(
            f"{BASE}/api/projects/{pid}/scan/status",
            params={"scan_id": scan_id}, headers=hdr()).json().get("status")
            in ("SUCCESS", "FAILURE", "completed"),
            desc="discovery scan")
        r = api.post(f"{BASE}/api/projects/{pid}/test-cases/generate", headers=hdr(), data={})
        assert r.ok, f"case generation failed: {r.status} {r.text()[:200]}"
        cases = wait_until(
            lambda: (lambda rows: rows if len(rows) >= 2 else None)(
                api.get(f"{BASE}/api/projects/{pid}/test-cases", headers=hdr()).json()),
            desc="test cases")
        assert len(cases) >= 2, f"expected several cases, got {len(cases)}"
        r = api.post(f"{BASE}/api/projects/{pid}/test-cases/approve", headers=hdr(), data={"refs": []})
        assert r.ok, f"approve-all failed: {r.status}"
        cats = {c["ref"]: c["category"] for c in cases}
        has_security = any(v == "security" for v in cats.values())
        has_functional = any(v == "functional" for v in cats.values())
        assert has_security and has_functional, f"need both security+functional cases, got {cats}"
        print(f"[2] discovery + {len(cases)} generated & approved cases "
              f"(functional={sum(1 for v in cats.values() if v=='functional')}, "
              f"security={sum(1 for v in cats.values() if v=='security')})")

        # ---------- 3. Groups tab: type picker + save group ----------
        page.goto(f"{BASE}/projects/{pid}", wait_until="networkidle")
        page.get_by_role("button").filter(has_text="Groups").first.click()
        page.wait_for_selector("text=Saved groups", timeout=10000)
        page.wait_for_selector("text=Type of test to run on this group", timeout=10000)

        # tick two cases via their checkboxes
        boxes = page.locator("label:has(input[type=checkbox]) input[type=checkbox]")
        assert boxes.count() >= 2, "expected test-case checkboxes in Groups tab"
        boxes.nth(0).check()
        boxes.nth(1).check()

        page.fill("input[placeholder='New group name…']", "Checkout security")
        # choose two test types from the chip row
        page.click("button:has-text('Security / authentication')")
        page.click("button:has-text('Functional')")
        hint = page.inner_text("text=The group will run only")
        assert "Security / authentication" in hint and "Functional" in hint, f"picker hint wrong: {hint}"
        page.click("button:has-text('Save selection as group')")

        row = page.locator("div", has_text="Checkout security").filter(
            has=page.get_by_text("cases")).first
        wait_until(lambda: page.locator("text=Checkout security").count() > 0, desc="group saved")
        body = page.inner_text("body")
        assert "Security / authentication" in body and "Functional" in body, \
            "type badges missing on the saved group row"
        print("[3] group saved with test types; badges visible ✅")

        # ---------- 4. Run the group → only matching categories ----------
        page.get_by_role("button", name="▶ Run group").first.click()
        page.wait_for_url("**/runs/**", timeout=15000)
        run_id = page.url.rstrip("/").split("/")[-1]
        suite = next(s for s in api.get(f"{BASE}/api/projects/{pid}/suites", headers=hdr()).json()
                     if s["name"] == "Checkout security")
        expected = {ref for ref, cat in cats.items()
                    if cat in ("security", "functional") and ref in suite["test_case_ids"]}

        def _run_refs() -> set | None:
            rows = api.get(f"{BASE}/api/projects/{pid}/test-runs/{run_id}/executions",
                           headers=hdr()).json()
            refs = {e["ref"] for e in rows}
            return refs if refs == expected else None
        refs_in_run = wait_until(_run_refs, timeout_s=60, desc="run executions")
        print(f"[4] group run respects test types: ran {sorted(refs_in_run)} ✅")

        # wait for the run to finish (needed for step 8)
        def run_done():
            rr = api.get(f"{BASE}/api/projects/{pid}/test-runs/{run_id}", headers=hdr()).json()
            return rr.get("status") in ("completed", "stopped", "failed")
        wait_until(run_done, timeout_s=300, desc="run completion")

        # ---------- 5. Executions tab: ad-hoc type filter ----------
        page.goto(f"{BASE}/projects/{pid}", wait_until="networkidle")
        page.get_by_role("button").filter(has_text="Executions").first.click()
        page.wait_for_selector("text=Group to run", timeout=10000)
        sel = page.locator("label:has-text('Type of test') select")
        sel.select_option("security")
        page.click("button:has-text('▶ Start run')")
        page.wait_for_timeout(1500)
        runs = api.get(f"{BASE}/api/projects/{pid}/test-runs", headers=hdr()).json()
        newest = runs[0]
        assert newest["config"].get("test_types") == ["security"], \
            f"ad-hoc type filter not sent: {newest['config']}"
        refs2 = {e["ref"] for e in api.get(
            f"{BASE}/api/projects/{pid}/test-runs/{newest['id']}/executions", headers=hdr()).json()}
        expected2 = {ref for ref, cat in cats.items() if cat == "security"}
        assert refs2 == expected2, f"ad-hoc run refs {refs2} != {expected2}"
        print(f"[5] Executions 'Type of test' filter works: ran {sorted(refs2)} ✅")

        # ---------- 6. Unknown test type via API → 422 ----------
        r = api.post(f"{BASE}/api/projects/{pid}/test-runs", headers=hdr(),
                     data={"test_types": ["load-test"]})
        assert r.status == 422, f"expected 422, got {r.status}"
        print("[6] unknown test type rejected with 422 ✅")

        # ---------- 7. Reports tab: inline viewer + open in browser ----------
        # step 5's UI start-run navigated to the run page — go back to the project
        page.goto(f"{BASE}/projects/{pid}", wait_until="networkidle")
        page.get_by_role("button").filter(has_text="Reports").first.click()
        page.wait_for_selector("text=Generate report", timeout=10000)
        page.locator("label:has-text('Format') select").select_option("html")
        page.click("button:has-text('Generate')")
        view_btn = wait_until(
            lambda: page.get_by_role("button", name="View", exact=True).first
            if page.get_by_role("button", name="View", exact=True).count() else None,
            timeout_s=180, desc="report View button")
        with page.expect_popup() as popup_info:
            page.get_by_role("button", name="Open in browser").first.click()
        popup = popup_info.value
        popup.wait_for_load_state()
        assert "AutoQA" in popup.content(), "report tab did not render report HTML"
        popup.close()
        view_btn.click()
        page.wait_for_selector("text=Report preview", timeout=10000)
        if page.locator("div.report-inline-view").count():
            frame_text = page.locator("div.report-inline-view").inner_text()
        else:
            frame_text = page.frame_locator("iframe").first.locator("body").inner_text()
        assert "Executive Summary" in frame_text, f"report content missing in viewer: {frame_text[:200]}"
        print("[7] HTML report: in-page viewer shows content; new-tab opens ✅")

        # ---------- 8. Executions tab: per-run View report ----------
        page.goto(f"{BASE}/projects/{pid}", wait_until="networkidle")
        page.get_by_role("button").filter(has_text="Executions").first.click()
        page.wait_for_selector("text=View report", timeout=10000)
        page.get_by_role("button", name="View report").first.click()
        page.wait_for_selector("text=Report preview", timeout=120000)
        page.get_by_role("button", name="Close").click()
        print("[8] per-run 'View report' opens the inline viewer ✅")

        # ---------- 9. Cleanup ----------
        r = api.delete(f"{BASE}/api/projects/{pid}", headers=hdr())
        assert r.status == 204, f"cleanup delete failed: {r.status}"
        print("[9] disposable project deleted (204) — GROUPS+REPORTS E2E PASSED ✅")

        page.screenshot(path="/tmp/autoqa-ui-groups.png", full_page=True)
        browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
