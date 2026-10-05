"""Worker tasks: website discovery scan (spec §3).

Progress is published to a Redis pub/sub channel so the API layer can stream
it to the UI (Phase 7 frontend wiring) without polling.
"""
import json
from datetime import datetime, timezone

import sqlalchemy as sa

from app.db import SessionLocal
from app.discovery.crawler import CrawlControls, Crawler
from app.models import Page, Project, ScanStatus
from app.tasks import celery_app


def _publish_progress(channel: str, event: dict) -> None:
    """Best-effort progress publish (works even before Phase 7 UI)."""
    try:
        from app.tasks import get_redis

        r = get_redis()
        if r is not None:
            r.publish(
                channel,
                json.dumps({**event, "ts": datetime.now(timezone.utc).isoformat()}),
            )
    except Exception:
        pass


@celery_app.task(name="app.run_scan", bind=True, max_retries=0)
def run_scan(self, scan_id: str, progress_channel: str, project_id: str, base_url: str, controls: dict) -> dict:
    """Execute the discovery crawl and persist Pages + captured requests."""
    db = SessionLocal()
    try:
        project = db.execute(sa.select(Project).where(Project.id == project_id)).scalar_one_or_none()
        if project is None:
            return {"scan_id": scan_id, "status": "failed", "error": "project not found"}

        def on_progress(count: int, url: str) -> None:
            channel = progress_channel or f"scan:{scan_id}"
            _publish_progress(channel, {"pages_found": count, "current": url})

        import asyncio

        # Authenticated discovery: decrypt credentials only inside the worker
        # execution path and hand the auth flow to the crawler.
        credentials: dict = {}
        auth: dict = {}
        if project.credentials_encrypted:
            try:
                from app.security.crypto import decrypt_json

                blob = decrypt_json(project.credentials_encrypted)
                credentials = blob.get("values", {}) or {}
                auth = blob.get("auth", {}) or {}
            except Exception:
                credentials, auth = {}, {}

        crawler = Crawler(CrawlControls(**controls), auth=auth, credentials=credentials)
        # Crawler is async; the Celery task is sync — drive a fresh loop.
        result = asyncio.run(crawler.crawl(base_url, progress_cb=on_progress))

        # Persist pages (dedupe by URL). The auth flow adds the login page to
        # result.pages AND the crawl may visit the same URL — the session has
        # autoflush disabled, so the per-row SELECT below cannot see pending
        # adds; merge duplicates within the batch first or the commit dies on
        # the uq_page_url unique constraint.
        batch: dict[str, dict] = {}
        for p in result.pages:
            url = p["url"]
            if url in batch:
                # Later rows (real crawl) win, but keep richer earlier data.
                for k, v in p.items():
                    if v or batch[url].get(k) in (None, "", {}):
                        batch[url][k] = v
            else:
                batch[url] = dict(p)
        for url, p in batch.items():
            existing = db.execute(
                sa.select(Page).where(Page.project_id == project_id, Page.url == url)
            ).scalar_one_or_none()
            page_values = {
                "title": p.get("title", ""),
                "depth": p.get("depth", 0),
                "status_code": p.get("status_code"),
                "components": p.get("components", {}),
            }
            if existing:
                for k, v in page_values.items():
                    setattr(existing, k, v)
            else:
                db.add(Page(project_id=project_id, url=url, **page_values))
        db.commit()

        # Stash captured requests for Phase 3 (API discovery)
        from app.tasks import get_redis

        r = get_redis()
        if r is not None:
            r.set(
                f"scan:{scan_id}:requests",
                json.dumps([r2.__dict__ for r2 in result.requests]),
                ex=86400,
            )

        # Remember the latest scan so /analyze reads the right evidence
        project.settings = {**(project.settings or {}), "last_scan_id": scan_id}
        db.commit()

        return {
            "scan_id": scan_id,
            "project_id": project_id,
            "status": "completed",
            "pages_found": len(result.pages),
            "requests_captured": len(result.requests),
            "external_links": len(result.external_links),
            "skipped": result.skipped_count,
        }
    except Exception as exc:
        _publish_progress(scan_id, {"error": str(exc)})
        return {"scan_id": scan_id, "status": "failed", "error": str(exc)}
    finally:
        db.close()


@celery_app.task(name="app.test_project_login", bind=True, max_retries=0)
def test_project_login(self, project_id: str) -> dict:
    """Try the project's login flow in a real browser and report pass/fail.

    Runs on the browser queue (Playwright image). Returns the auth log and a
    final URL — never credential material."""
    import asyncio

    db = SessionLocal()
    try:
        project = db.execute(
            sa.select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()
        if project is None:
            return {"status": "failed", "error": "project not found"}
        if not project.credentials_encrypted:
            return {"status": "failed", "error": "no credentials configured"}

        from app.security.crypto import decrypt_json

        blob = decrypt_json(project.credentials_encrypted)
        credentials = blob.get("values", {}) or {}
        auth = blob.get("auth", {}) or {}
        if not auth.get("login_url"):
            return {"status": "failed", "error": "no login_url configured"}

        from playwright.async_api import async_playwright

        from app.execution.auth_flow import browser_login_async

        async def _try():
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                log: list = []
                ok = await browser_login_async(
                    page, auth, credentials, dict(credentials), log
                )
                # A successful submit triggers a navigation; wait for the new
                # document to settle before reading url/title, otherwise the
                # execution context is destroyed mid-navigation.
                try:
                    await page.wait_for_load_state("load", timeout=10000)
                    await page.wait_for_timeout(300)
                except Exception:  # noqa: BLE001
                    pass
                final_url = page.url
                try:
                    final_title = await page.title()
                except Exception:  # noqa: BLE001
                    final_title = ""
                await browser.close()
                return ok, final_url, final_title, log

        try:
            ok, final_url, final_title, log = asyncio.run(_try())
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:300]}

        return {
            "status": "completed",
            "login_ok": ok,
            "final_url": final_url,
            "final_title": final_title[:120],
            "log": log,
        }
    finally:
        db.close()


_CSS_FOR_EL_JS = """
(el) => {
  if (!el) return null;
  if (el.id) return '#' + CSS.escape(el.id);
  const name = el.getAttribute('name');
  if (name) return el.tagName.toLowerCase() + "[name='" + name + "']";
  const testid = el.getAttribute('data-testid') || el.getAttribute('data-test');
  if (testid) return '[data-testid="' + testid + '"]';
  const ph = el.getAttribute('placeholder');
  if (ph) return el.tagName.toLowerCase() + "[placeholder='" + ph.split("'")[0] + "']";
  if (el.getAttribute('type')) return el.tagName.toLowerCase() + "[type='" + el.getAttribute('type') + "']";
  return el.tagName.toLowerCase();
}
"""


_DETECT_LOGIN_JS = """
() => {
  const css = %CSS_FN%;
  const visible = (el) => el && !el.disabled && el.offsetParent !== null;
  const out = { username_selector: null, password_selector: null, submit_selector: null, page_title: document.title };

  const pwds = Array.from(document.querySelectorAll("input[type='password']")).filter(visible);
  const pwd = pwds[0] || null;
  if (pwd) out.password_selector = css(pwd);

  // Username: the last visible text/email/tel input at or before the password field
  const textInputs = Array.from(
    document.querySelectorAll("input[type='text'], input[type='email'], input[type='tel'], input:not([type])")
  ).filter((el) => visible(el) && el.type !== 'hidden');
  if (textInputs.length) {
    let best = textInputs[0];
    for (const el of textInputs) {
      if (!pwd || pwd.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING) best = el;
    }
    out.username_selector = css(best);
  }

  // Submit: inside the same form if possible, else the most button-like element
  const scope = (pwd && pwd.closest('form')) || (textInputs[0] && textInputs[0].closest('form')) || document;
  const submit =
    scope.querySelector("button[type='submit'], input[type='submit']") ||
    Array.from(scope.querySelectorAll('button')).find(visible) ||
    null;
  if (submit) out.submit_selector = css(submit);

  return out;
}
""".replace("%CSS_FN%", _CSS_FOR_EL_JS.strip())


@celery_app.task(name="app.detect_login_selectors", bind=True, max_retries=0)
def detect_login_selectors(self, project_id: str, login_url: str) -> dict:
    """Open the login page in a real browser and guess the form selectors.

    Pure read-only detection — nothing is filled, submitted or saved. The UI
    shows the proposals and the user still presses Save."""
    import asyncio

    from app.security.ssrf import validate_target_url

    try:
        login_url = validate_target_url(login_url)
    except ValueError as exc:
        return {"status": "failed", "error": f"login url rejected: {exc}"}

    from playwright.async_api import async_playwright

    async def _detect():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(login_url, timeout=20000, wait_until="networkidle")
            await page.wait_for_timeout(400)  # SPA settle
            found = await page.evaluate(_DETECT_LOGIN_JS)
            # Verify each selector actually resolves (count via the same engine)
            for key in ("username_selector", "password_selector", "submit_selector"):
                sel = found.get(key)
                if sel:
                    try:
                        if await page.locator(sel).count() == 0:
                            found[key] = None
                    except Exception:
                        found[key] = None
            await browser.close()
            return found

    try:
        found = asyncio.run(_detect())
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"could not open the login page: {str(exc)[:200]}"}

    return {
        "status": "completed",
        "login_url": login_url,
        **found,
    }


@celery_app.task(name="app.dry_run_login", bind=True, max_retries=0)
def dry_run_login(
    self,
    project_id: str,
    auth_override: dict | None = None,
    credentials_override: dict | None = None,
) -> dict:
    """Dry run of the login flow with the form's current values.

    auth_override/credentials_override carry what the user typed but may not
    have saved yet; anything omitted falls back to the project's saved config.
    Overrides are NOT persisted. The returned log contains selectors and
    outcomes only — credential values never appear in it."""
    import asyncio

    db = SessionLocal()
    try:
        project = db.execute(
            sa.select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()
        if project is None:
            return {"status": "failed", "error": "project not found"}

        from app.security.crypto import decrypt_json

        credentials: dict = {}
        auth: dict = {}
        if project.credentials_encrypted:
            blob = decrypt_json(project.credentials_encrypted)
            credentials = blob.get("values", {}) or {}
            auth = blob.get("auth", {}) or {}

        for k, v in (auth_override or {}).items():
            if v is not None:
                auth[k] = v
        for k, v in (credentials_override or {}).items():
            if v:
                credentials[k] = v

        if not auth.get("login_url"):
            return {"status": "failed", "error": "no login_url provided"}
        if not credentials.get("username") or not credentials.get("password"):
            return {"status": "failed", "error": "username and password are required for a dry run"}

        # Dry runs default to the standard assertion when none is set, so the
        # result actually means something instead of passing vacuously.
        auth.setdefault("success_assert", "url_not_contains:login")

        from playwright.async_api import async_playwright

        from app.execution.auth_flow import browser_login_async

        async def _try():
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                log: list = []
                ok = await browser_login_async(
                    page, auth, credentials, dict(credentials), log
                )
                # A successful submit triggers a navigation; wait for the new
                # document to settle before reading url/title, otherwise the
                # execution context is destroyed mid-navigation.
                try:
                    await page.wait_for_load_state("load", timeout=10000)
                    await page.wait_for_timeout(300)
                except Exception:  # noqa: BLE001
                    pass
                final_url = page.url
                try:
                    final_title = await page.title()
                except Exception:  # noqa: BLE001
                    final_title = ""
                await browser.close()
                return ok, final_url, final_title, log

        try:
            ok, final_url, final_title, log = asyncio.run(_try())
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:300]}

        return {
            "status": "completed",
            "login_ok": ok,
            "final_url": final_url,
            "final_title": final_title[:120],
            "log": log,
        }
    finally:
        db.close()
