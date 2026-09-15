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

        page.screenshot(path="/tmp/autoqa-ui.png", full_page=True)
        print("screenshot: /tmp/autoqa-ui.png")

        browser.close()
    print("E2E UI CHECK PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
