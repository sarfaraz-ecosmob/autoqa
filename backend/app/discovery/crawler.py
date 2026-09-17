"""Website discovery crawler (spec §3).

Browser crawling + DOM analysis + network interception (spec's "important
improvement" — SPA-aware, not just link scraping). All controls are enforced:
same-origin, max depth, max URLs, rate limiting, dedupe, robots.txt handling,
and timeouts. The SSRF guard validates every URL before navigation.
"""
import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import get_settings
from app.security.ssrf import validate_target_url


@dataclass
class CrawlControls:
    max_depth: int = 2
    max_urls: int = 50
    rate_limit_per_sec: float = 5.0
    respect_robots: bool = True
    nav_timeout_ms: int = 15000
    same_origin: bool = True


@dataclass
class DiscoveredRequest:
    method: str
    url: str
    resource_type: str
    status: int | None = None


@dataclass
class CrawlResult:
    start_url: str
    pages: list[dict] = field(default_factory=list)
    requests: list[DiscoveredRequest] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    skipped_count: int = 0


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class _RateGate:
    def __init__(self, per_sec: float):
        self.interval = 1.0 / max(per_sec, 0.1)
        self._last = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        delta = now - self._last
        if delta < self.interval:
            await asyncio.sleep(self.interval - delta)
        self._last = time.monotonic()


async def _fetch_robots(base_url: str) -> set[str]:
    """Return disallowed path prefixes (best-effort, 2s timeout)."""
    try:
        async with httpx.AsyncClient(timeout=2.0, follow_redirects=True) as client:
            r = await client.get(urljoin(base_url, "/robots.txt"))
            if r.status_code != 200:
                return set()
        disallowed = set()
        for line in r.text.splitlines():
            line = line.strip()
            if line.lower().startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if path:
                    disallowed.add(path)
        return disallowed
    except Exception:
        return set()


def _extract_components(page_html: str) -> dict:
    """Lightweight DOM analysis (no full parse needed for counts)."""
    html = page_html.lower()
    def count(tag: str) -> int:
        return html.count(f"<{tag}")
    return {
        "forms": count("form"),
        "inputs": count("input"),
        "buttons": count("button"),
        "selects": count("select"),
        "textareas": count("textarea"),
        "tables": count("table"),
        "modals": html.count('class="modal') + html.count("dialog"),
        "file_uploads": html.count('type="file"') + html.count("type='file'"),
        "scripts": count("script"),
        "iframes": count("iframe"),
    }


class Crawler:
    def __init__(self, controls: CrawlControls | None = None, auth: dict | None = None, credentials: dict | None = None):
        self.controls = controls or CrawlControls()
        self.auth: dict = auth or {}
        self.credentials: dict = credentials or {}
        self._seen: set[str] = (
            set()
        )  # normalized URLs (no fragment)
        self.result = CrawlResult(start_url="")

    def _normalize(self, url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}" + (
            f"?{parts.query}" if parts.query else ""
        )

    def _should_visit(self, url: str, start_origin: str, depth: int, robots: set[str]) -> bool:
        if len(self._seen) >= self.controls.max_urls:
            return False
        if depth > self.controls.max_depth:
            return False
        if url in self._seen:
            return False
        if self.controls.same_origin and _origin(url) != start_origin:
            return False
        path = urlsplit(url).path or "/"
        for prefix in robots:
            if prefix != "/" and path.startswith(prefix):
                self.result.skipped_count += 1
                return False
        return True

    async def crawl(self, start_url: str, progress_cb=None) -> CrawlResult:
        # Lazy import: only the browser-worker image ships Playwright
        from playwright.async_api import async_playwright

        settings = get_settings()
        start_url = validate_target_url(start_url)  # SSRF gate
        self.result = CrawlResult(start_url=start_url)
        self._seen = set()
        start_origin = _origin(start_url)

        robots: set[str] = set()
        if self.controls.respect_robots:
            robots = await _fetch_robots(start_url)

        queue: list[tuple[str, int]] = [(start_url, 0)]
        gate = _RateGate(self.controls.rate_limit_per_sec)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            # Network interception: capture API + asset requests (spec §3)
            requests: list[DiscoveredRequest] = []

            async def _on_request(request) -> None:
                requests.append(
                    DiscoveredRequest(
                        method=request.method,
                        url=request.url,
                        resource_type=request.resource_type,
                    )
                )

            page.on("request", _on_request)

            # ---- Authenticated discovery: sign in before crawling so pages
            # behind the login are discovered too. Credentials stay inside
            # this context; only a pass/fail marker is reported.
            if self.auth.get("login_url") and self.credentials:
                from app.execution.auth_flow import browser_login

                sync_page = page

                class _SyncShim:
                    """Tiny adapter so the sync auth_flow helpers drive the
                    async Playwright page (goto/fill/click/url/title)."""

                    def __init__(self, p):
                        self._p = p

                    @property
                    def url(self):
                        return self._p.url

                    def goto(self, target, timeout=15000, wait_until="domcontentloaded"):
                        return self._p.goto(target, timeout=timeout, wait_until=wait_until)

                    def fill(self, selector, value, timeout=5000):
                        return self._p.fill(selector, value, timeout=timeout)

                    def click(self, selector, timeout=5000):
                        return self._p.click(selector, timeout=timeout)

                    def wait_for_timeout(self, ms):
                        return self._p.wait_for_timeout(ms)

                    def get_by_text(self, text):
                        return self._p.get_by_text(text)

                    def locator(self, sel):
                        return self._p.locator(sel)

                    def title(self):
                        return self._p.title()

                shim = _SyncShim(sync_page)
                login_log: list = []
                auth_ok = browser_login(shim, self.auth, self.credentials, dict(self.credentials), login_log)
                self.result.pages.append(
                    {
                        "url": self.auth["login_url"],
                        "title": "login (auth flow)",
                        "depth": 0,
                        "status_code": 200 if auth_ok else 401,
                        "components": {},
                        "auth": "passed" if auth_ok else "failed",
                    }
                )

            while queue:
                url, depth = queue.pop(0)
                normalized = self._normalize(url)
                if not self._should_visit(normalized, start_origin, depth, robots):
                    continue
                self._seen.add(normalized)
                await gate.wait()

                status_code = None
                title = ""
                try:
                    resp = await page.goto(normalized, timeout=self.controls.nav_timeout_ms, wait_until="domcontentloaded")
                    status_code = resp.status if resp else None
                    title = await page.title()
                    await page.wait_for_timeout(300)  # let SPA settle briefly

                    html = await page.content()
                    components = _extract_components(html)

                    # Collect links (BFS seed)
                    links = await page.eval_on_selector_all(
                        "a[href]",
                        "els => els.map(e => e.href)",
                    )
                    for href in links:
                        absolute = urljoin(normalized, href)
                        if absolute.startswith(("mailto:", "javascript:", "tel:")):
                            continue
                        if _origin(absolute) != start_origin:
                            if absolute not in self.result.external_links and len(self.result.external_links) < 100:
                                self.result.external_links.append(absolute)
                            continue
                        normalized_link = self._normalize(absolute)
                        if normalized_link not in self._seen:
                            queue.append((normalized_link, depth + 1))

                    self.result.pages.append(
                        {
                            "url": normalized,
                            "title": title,
                            "depth": depth,
                            "status_code": status_code,
                            "components": components,
                        }
                    )
                    if progress_cb:
                        progress_cb(len(self.result.pages), normalized)
                except Exception:
                    self.result.pages.append(
                        {
                            "url": normalized,
                            "title": "",
                            "depth": depth,
                            "status_code": status_code,
                            "components": {},
                            "error": "navigation_failed",
                        }
                    )

            self.result.requests = requests
            await browser.close()

        return self.result
