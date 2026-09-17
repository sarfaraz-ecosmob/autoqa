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

        # Persist pages (dedupe by URL)
        for p in result.pages:
            existing = db.execute(
                sa.select(Page).where(Page.project_id == project_id, Page.url == p["url"])
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
                db.add(Page(project_id=project_id, url=p["url"], **page_values))
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
