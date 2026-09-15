"""Environment-driven configuration. Never hard-code secrets (spec §33)."""
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    environment: str = "development"
    secret_key: str = "dev_only_change_me"

    # Queue / cache (Phase 0)
    redis_url: str = "redis://redis:6379/0"

    # Database (used from Phase 1)
    database_url: str = "postgresql+psycopg://autoqa:autoqa_dev_password@postgres:5432/autoqa"

    # Object storage (used from Phase 1)
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "autoqa"
    minio_secret_key: str = "autoqa_dev_password"
    minio_bucket: str = "autoqa-artifacts"

    # Realtime (Phase 7)
    ws_heartbeat_seconds: int = 15

    model_config = {"env_file": ".env", "env_prefix": "AUTOQA_"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
