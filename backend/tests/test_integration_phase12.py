"""Phase 12 integration: report API flow against the live DB.

Runs inside the backend container; skips when seed data (live gates) is absent.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _auth_and_project(client) -> tuple[dict, str]:
    r = await client.post(
        "/api/auth/login",
        json={"email": "live@example.com", "password": "S3curePass!xyz"},
    )
    if r.status_code != 200:
        pytest.skip("seeded live user not present (run live gates first)")
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.get("/api/projects", headers=headers)
    projects = [p for p in r.json() if p["base_url"].startswith("http://demo-app")]
    if not projects:
        pytest.skip("no demo project in DB")
    return headers, projects[0]["id"]


async def test_report_lifecycle_json(client):
    """Create → poll → download a JSON report through the full API flow."""
    headers, project_id = await _auth_and_project(client)

    r = await client.post(
        f"/api/projects/{project_id}/reports",
        headers=headers,
        json={"format": "json"},
    )
    assert r.status_code == 202, r.text
    report_id = r.json()["report_id"]

    # Poll up to ~20s for the worker to finish (task runs in the reports queue)
    import asyncio

    status = "pending"
    for _ in range(20):
        await asyncio.sleep(1)
        r = await client.get(f"/api/projects/{project_id}/reports/{report_id}", headers=headers)
        status = r.json()["status"]
        if status in ("completed", "failed"):
            break
    assert status == "completed", r.text

    r = await client.get(
        f"/api/projects/{project_id}/reports/{report_id}/download", headers=headers
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers["content-disposition"]

    import json

    data = json.loads(r.content)
    assert data["scope"]["project_id"] == project_id
    assert "executive_summary" in data and "recommendations" in data


async def test_report_invalid_format_422(client):
    headers, project_id = await _auth_and_project(client)
    r = await client.post(
        f"/api/projects/{project_id}/reports",
        headers=headers,
        json={"format": "docx"},
    )
    assert r.status_code == 422


async def test_report_unknown_run_404(client):
    headers, project_id = await _auth_and_project(client)
    r = await client.post(
        f"/api/projects/{project_id}/reports",
        headers=headers,
        json={"format": "pdf", "test_run_id": "nonexistent"},
    )
    assert r.status_code == 404


async def test_reports_require_auth(client):
    r = await client.get("/api/projects/whatever/reports")
    assert r.status_code == 401
