"""E2E UI check (run inside browser-worker): drives the real UI in Chromium.

Verifies: login page renders → sign-in works → project list shows → project
detail tabs render. Uses the seeded user/project from the live gates.
"""
import sys

from playwright.sync_api import sync_playwright

BASE = "http://nginx"
EMAIL = "live@example.com"
PASSWORD = "S3curePass!xyz"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # 1. Login page renders
        page.goto(f"{BASE}/login", wait_until="networkidle")
        assert page.locator("text=Sign in").first.is_visible(), "login page did not render"
        print("[1] login page renders OK")

        # 2. Sign in
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(f"{BASE}/", timeout=10000)
        print("[2] sign-in OK, redirected to projects")

        # 3. Project list shows the demo project
        page.wait_for_selector("text=Demo Scan", timeout=10000)
        print("[3] project list shows 'Demo Scan' OK")

        # 4. Open project detail
        page.click("text=Demo Scan")
        page.wait_for_selector("text=Test cases", timeout=10000)
        for tab in ["discovery", "apis", "test plan", "test cases"]:
            page.click(f"text={tab.capitalize()}")
            page.wait_for_timeout(400)
        print("[4] project detail tabs render OK")

        # 5. Quality tab shows accessibility + performance modules (Phase 11)
        page.click("text=Quality")
        page.wait_for_selector("text=Accessibility (WCAG)", timeout=5000)
        page.wait_for_selector("text=Performance (smoke load)", timeout=5000)
        print("[5] quality tab modules render OK")

        # 6. Reports tab shows generation UI + report history (Phase 12)
        page.click("text=Reports")
        page.wait_for_selector("text=Generate report", timeout=5000)
        page.wait_for_selector("text=Whole project", state="attached", timeout=5000)
        print("[6] reports tab renders OK")

        # 7. Run page renders (latest run — the matrix gate run)
        page.click("text=Executions")
        page.wait_for_selector("text=View live", timeout=10000)
        page.click("text=View live")
        page.wait_for_selector("text=Live log", timeout=10000)
        page.wait_for_timeout(1500)
        if page.locator("text=Cross-browser matrix").count() > 0:
            print("[7] run page + cross-browser matrix render OK")
        else:
            print("[7] run page renders OK (matrix hidden for runs without data)")

        page.screenshot(path="/tmp/autoqa-ui.png", full_page=True)
        print("screenshot: /tmp/autoqa-ui.png")

        # 8. Regression (authorization gate): create a project via the UI form
        #    with the authorization checkbox ticked — it must be immediately
        #    scannable (badge 'authorized', no amber banner).
        stamp = __import__("time").strftime("%H%M%S")
        proj_name = f"E2E AuthGate {stamp}"
        page.goto(f"{BASE}/", wait_until="networkidle")
        page.click("text=+ New project")
        page.fill("input[type=url]", "http://demo-app:9000")
        page.fill("form input:not([type=url]):not([type=checkbox])", proj_name)
        page.check("input[type=checkbox]")
        page.click("button[type=submit]")
        card = page.locator("a", has_text=proj_name).first
        card.wait_for(timeout=10000)
        assert card.locator("text=authorized").count() > 0, (
            "project created without authorization — checkbox value lost"
        )
        print("[8] project created via UI with authorization OK")

        # 9. Open it — Discovery tab must show the scan controls (no banner)
        card.click()
        page.wait_for_selector("text=Overview", timeout=10000)
        page.click("text=Discovery")
        page.wait_for_selector("text=Start scan", timeout=10000)
        assert page.locator("text=Authorization must be confirmed").count() == 0, (
            "amber authorization banner shown for a confirmed project"
        )
        print("[9] discovery tab shows scan controls for confirmed project OK")

        # 10. Run a real discovery scan from the UI
        page.click("text=Start scan")
        page.wait_for_selector("text=completed", timeout=120000)
        page.wait_for_selector("text=Discovered pages", timeout=10000)
        assert "No pages discovered yet" not in page.content(), "scan found no pages"
        print("[10] discovery scan from UI completed with pages OK")

        # 11. Cleanup: delete the created project
        page.goto(f"{BASE}/", wait_until="networkidle")
        page.once("dialog", lambda d: d.accept())
        page.locator("a", has_text=proj_name).locator("text=Delete").click()
        page.wait_for_timeout(1000)
        assert page.locator("a", has_text=proj_name).count() == 0, "cleanup delete failed"
        print("[11] cleanup delete OK")

        page.screenshot(path="/tmp/autoqa-ui2.png", full_page=True)
        print("screenshot: /tmp/autoqa-ui2.png")

        browser.close()
    print("E2E UI CHECK PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
