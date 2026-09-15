"""FastAPI application factory. Phase 0 scope: health + hello + worker ping."""
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AutoQA Platform API",
        version="0.1.0",
        description="AI-powered autonomous QA platform (see PLAN.md).",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # nginx same-origin in prod; tightened in Phase 15
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "environment": settings.environment}

    @app.get("/api/hello", tags=["system"])
    def hello(name: str = "world") -> dict:
        return {"message": f"Hello, {name}!", "phase": 0}

    @app.get("/api/worker-ping", tags=["system"])
    def worker_ping(
        result: dict = Depends(dispatch_ping),
    ) -> dict:
        return result

    return app


def dispatch_ping() -> dict:
    """Send a Celery task and wait briefly; used by the Phase 0 docker gate."""
    from app.tasks import ping

    try:
        async_result = ping.apply_async()
        return {"status": "ok", "pong": async_result.get(timeout=10)}
    except Exception as exc:  # pragma: no cover - surfaced to the caller
        return {"status": "error", "detail": str(exc)}


app = create_app()
