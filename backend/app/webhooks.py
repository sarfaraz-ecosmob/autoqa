"""Outbound webhook delivery (Tier-1).

Webhook URLs are Fernet-encrypted at rest (like all our secrets) and deliveries
are HMAC-SHA256 signed so receivers can verify authenticity:

    X-AutoQA-Signature: sha256=<hmac(secret, timestamp + '.' + body)>

Delivery is best-effort: failures update `last_status` on the webhook row but
never break the emitting pipeline. Slack-style receivers get a `text` field
for pretty rendering; the full JSON payload is always included.
"""
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa

from app.db import SessionLocal
from app.models import Webhook
from app.security.crypto import decrypt_secret

TIMEOUT_SECONDS = 8.0


def sign(payload: bytes, secret: str, timestamp: str) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.{payload.decode()}".encode(), hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def build_payload(event: dict) -> dict:
    """Slack-compatible envelope: `text` renders in chat, `event` has details."""
    kind = event.get("kind", "event")
    title = event.get("title", "AutoQA event")
    text = f"*{title}*"
    if event.get("body"):
        text += f"\n{event['body']}"
    if event.get("link"):
        text += f"\n{event['link']}"
    return {"text": text, "event": {**event, "kind": kind}}


def deliver(webhook_id: str, event: dict) -> dict:
    """Deliver one event to one webhook. Never raises."""
    db = SessionLocal()
    try:
        row = db.get(Webhook, webhook_id)
        if row is None or not row.is_active:
            return {"ok": False, "reason": "webhook missing or inactive"}
        url = decrypt_secret(row.url_encrypted) if row.url_encrypted else ""
        if not url:
            return {"ok": False, "reason": "webhook has no URL"}

        body = json.dumps(build_payload(event)).encode()
        ts = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-AutoQA-Event": event.get("kind", "event"),
            "X-AutoQA-Timestamp": ts,
        }
        if row.secret:
            headers["X-AutoQA-Signature"] = sign(body, row.secret, ts)

        ok, status = False, ""
        try:
            resp = httpx.post(url, content=body, headers=headers, timeout=TIMEOUT_SECONDS)
            ok = 200 <= resp.status_code < 300
            status = "ok" if ok else f"error:{resp.status_code}"
        except httpx.HTTPError as exc:
            status = f"error:{type(exc).__name__}"

        row.last_status = status[:30]
        row.last_delivery_at = datetime.now(timezone.utc)
        db.commit()
        return {"ok": ok, "status": status}
    finally:
        db.close()


def emit(project_id: str, event: dict) -> int:
    """Fan an event out to every active webhook of the project subscribed to
    its kind. Called from workers/API after notable happenings."""
    db = SessionLocal()
    try:
        rows = db.execute(
            sa.select(Webhook.id).where(
                Webhook.project_id == project_id,
                Webhook.is_active.is_(True),
            )
        ).scalars().all()
    finally:
        db.close()

    kind = event.get("kind", "")
    count = 0
    for webhook_id in rows:
        db = SessionLocal()
        try:
            row = db.get(Webhook, webhook_id)
            subscribed = row.events or []
            db.close()
        except Exception:
            db.close()
            continue
        if subscribed and kind not in subscribed:
            continue
        try:
            result = deliver(webhook_id, event)
            if result.get("ok"):
                count += 1
        except Exception:
            pass
    return count
