"""Celery application and Phase 0 smoke tasks."""
from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "autoqa",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_default_queue="autoqa",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)


@celery_app.task(name="app.ping")
def ping() -> dict:
    """Heartbeat task proving the broker/worker round-trip works."""
    return {"pong": True, "worker": "celery@autoqa"}
