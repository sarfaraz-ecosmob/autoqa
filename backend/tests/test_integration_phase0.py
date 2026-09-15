"""Phase 0 integration test: FastAPI endpoints via httpx ASGI transport.

Runs inside the backend container (docker gate for Phase 0).
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


async def test_hello(client):
    r = await client.get("/api/hello", params={"name": "docker"})
    assert r.status_code == 200
    assert r.json()["message"] == "Hello, docker!"


async def test_worker_ping_roundtrip(client):
    """Requires redis + worker running — this is the Phase 0 docker gate."""
    r = await client.get("/api/worker-ping")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["pong"]["pong"] is True  # task returns {pong, worker}
