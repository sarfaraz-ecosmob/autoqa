"""Phase 11 integration: quality endpoints against the live DB/API.

Runs inside the backend container; skips gracefully when seed data
(live gates) is absent.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _auth_headers(client) -> dict:
    r = await client.post(
        "/api/auth/login",
        json={"email": "live@example.com", "password": "S3curePass!xyz"},
    )
    if r.status_code != 200:
        pytest.skip("seeded live user not present (run live gates first)")
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _project_id(client, headers) -> str:
    r = await client.get("/api/projects", headers=headers)
    projects = [p for p in r.json() if p["base_url"].startswith("http://demo-app")]
    return projects[0]["id"] if projects else ""


async def test_matrix_endpoint_shape(client):
    headers = await _auth_headers(client)
    project_id = await _project_id(client, headers)
    if not project_id:
        pytest.skip("no demo project in DB")

    r = await client.get(f"/api/projects/{project_id}/quality/browser-matrix", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["browsers"] == ["chromium", "firefox", "webkit"]
    for row in body["rows"]:
        assert set(body["browsers"]) <= set(row)


async def test_matrix_with_unknown_run_404(client):
    headers = await _auth_headers(client)
    project_id = await _project_id(client, headers)
    if not project_id:
        pytest.skip("no demo project in DB")

    r = await client.get(
        f"/api/projects/{project_id}/quality/browser-matrix?run_id=nonexistent",
        headers=headers,
    )
    assert r.status_code == 404


async def test_a11y_and_perf_results_endpoints(client):
    headers = await _auth_headers(client)
    project_id = await _project_id(client, headers)
    if not project_id:
        pytest.skip("no demo project in DB")

    r = await client.get(f"/api/projects/{project_id}/quality/a11y/results", headers=headers)
    assert r.status_code == 200
    for row in r.json():
        assert "violation_count" in row and "by_severity" in row

    r = await client.get(f"/api/projects/{project_id}/quality/perf/results", headers=headers)
    assert r.status_code == 200
    for row in r.json():
        assert {"p50_ms", "p95_ms", "p99_ms", "throughput_rps", "error_rate"} <= set(row)


async def test_quality_requires_auth(client):
    r = await client.get("/api/projects/whatever/quality/browser-matrix")
    assert r.status_code == 401
