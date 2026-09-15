"""Environment-driven configuration. Never hard-code secrets (spec §33)."""
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    environment: str = "development"
    secret_key: str = "dev_only_change_me_NOT_FOR_PRODUCTION_USE"

    # Auth
    jwt_algorithm: str = "HS256"
    jwt_access_minutes: int = 30
    jwt_refresh_days: int = 7

    # Queue / cache
    redis_url: str = "redis://redis:6379/0"

    # Database
    database_url: str = "postgresql+psycopg://autoqa:autoqa_dev_password@postgres:5432/autoqa"

    # Object storage: "fs" (default) or "s3" (S3-compatible endpoint e.g. MinIO)
    storage_driver: str = "fs"
    storage_dir: str = "/data/artifacts"
    s3_endpoint_url: str = "http://minio:9000"
    s3_bucket: str = "autoqa-artifacts"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"

    # Pluggable LLM (spec §1): "none" (heuristic fallbacks) or "openai" (any
    # OpenAI-compatible API, including locally hosted vLLM/Ollama gateways)
    llm_provider: str = "none"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 60

    # Realtime (Phase 7)
    ws_heartbeat_seconds: int = 15

    model_config = {"env_file": ".env", "env_prefix": "AUTOQA_"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
