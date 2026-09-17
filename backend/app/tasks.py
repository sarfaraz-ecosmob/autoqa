"""Celery application, shared Redis client, and tasks."""
from celery import Celery
from redis import Redis

from app.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "autoqa",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=["app.tasks", "app.worker_tasks", "app.worker_exec", "app.worker_security", "app.worker_quality", "app.worker_reports", "app.worker_schedules"],
)

celery_app.conf.update(
    task_default_queue="autoqa",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_routes={
        "app.run_scan": {"queue": "browser"},
        "app.execute_test": {"queue": "browser"},
        "app.run_security_scan": {"queue": "security"},
        "app.run_a11y_audit": {"queue": "browser"},
        "app.run_perf_test": {"queue": "browser"},
        "app.generate_report": {"queue": "reports"},
        "app.dispatch_due_schedules": {"queue": "autoqa"},
        "app.*": {"queue": "autoqa"},
    },
)

# Tier-1: cron scheduler tick (scheduler service runs celery beat)
celery_app.conf.beat_schedule = {
    "dispatch-due-schedules": {
        "task": "app.dispatch_due_schedules",
        "schedule": 60.0,
    },
}

_redis_client: Redis | None = None


def get_redis() -> Redis | None:
    """Shared Redis connection (None only if redis is unreachable)."""
    global _redis_client
    if _redis_client is None:
        try:
            client = Redis.from_url(_settings.redis_url, socket_connect_timeout=2)
            client.ping()
            _redis_client = client
        except Exception:
            return None
    return _redis_client


@celery_app.task(name="app.ping")
def ping() -> dict:
    """Heartbeat task proving the broker/worker round-trip works."""
    return {"pong": True, "worker": "celery@autoqa"}
