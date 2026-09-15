"""Unit tests for Phase 0: app wiring, config, and task definitions."""
import json

from app.config import Settings
from app.main import create_app
from app.tasks import ping


def test_settings_defaults_are_env_driven():
    s = Settings(redis_url="redis://override:6379/0")  # init kwargs use field names
    assert s.redis_url == "redis://override:6379/0"
    assert s.environment == "development"


def test_app_routes_registered():
    app = create_app()
    paths = {route.path for route in app.routes}
    assert {"/api/health", "/api/hello", "/api/worker-ping"} <= paths


def test_ping_task_is_registered_and_callable():
    assert ping.name == "app.ping"
    # Direct (eager) invocation without a broker
    assert json.loads(json.dumps(ping()))["pong"] is True
