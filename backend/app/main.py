"""FastAPI application — Phase 1 wiring (auth, projects, health, metrics)."""
import time

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import analyze as analyze_routes
from app.api import auth as auth_routes
from app.api import projects as project_routes
from app.api import quality as quality_routes
from app.api import reports as report_routes
from app.api import scans as scan_routes
from app.api import history as history_routes
from app.api import failures as failure_routes
from app.api import realtime as realtime_routes
from app.api import security as security_routes
from app.api import settings as settings_routes
from app.api import testdata as testdata_routes
from app.api import testcases as testcase_routes
from app.api import testplans as testplan_routes
from app.api import testruns as testrun_routes
from app.api import tier1 as tier1_routes
from app.api.deps import get_current_user
from app.config import get_settings
from app.models import User


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AutoQA Platform API",
        version="0.2.0",
        description="AI-powered autonomous QA platform (see PLAN.md).",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # nginx same-origin in prod; tightened in Phase 15
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- simple in-process metrics (spec §26; Prometheus scrape lands Phase 7) ----
    _metrics: dict[str, float] = {"requests_total": 0.0, "errors_total": 0.0, "latency_sum_ms": 0.0}

    @app.middleware("http")
    async def _count_requests(request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
            if response.status_code >= 500:
                _metrics["errors_total"] += 1
            return response
        finally:
            _metrics["requests_total"] += 1
            _metrics["latency_sum_ms"] += (time.perf_counter() - start) * 1000

    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "environment": settings.environment}

    @app.get("/api/hello", tags=["system"])
    def hello(name: str = "world") -> dict:
        return {"message": f"Hello, {name}!", "phase": 1}

    @app.get("/api/worker-ping", tags=["system"])
    def worker_ping() -> dict:
        """Celery round-trip smoke check (Phase 0 docker gate)."""
        from app.tasks import ping

        try:
            async_result = ping.apply_async()
            return {"status": "ok", "pong": async_result.get(timeout=10)}
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}

    @app.get("/api/metrics", tags=["system"])
    def metrics() -> dict:
        total = _metrics["requests_total"]
        return {
            "qa_requests_total": total,
            "qa_errors_total": _metrics["errors_total"],
            "qa_avg_latency_ms": round(_metrics["latency_sum_ms"] / total, 2) if total else 0.0,
        }

    app.include_router(auth_routes.router)
    app.include_router(project_routes.router)
    app.include_router(scan_routes.router)
    app.include_router(analyze_routes.router)
    app.include_router(testplan_routes.router)
    app.include_router(testcase_routes.router)
    app.include_router(testrun_routes.router)
    app.include_router(realtime_routes.router)
    app.include_router(failure_routes.router)
    app.include_router(security_routes.router)
    app.include_router(quality_routes.router)
    app.include_router(report_routes.router)
    app.include_router(history_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(testdata_routes.router)
    app.include_router(tier1_routes.router)
    from app.api import requirements as requirement_routes

    app.include_router(requirement_routes.router)

    return app


app = create_app()
