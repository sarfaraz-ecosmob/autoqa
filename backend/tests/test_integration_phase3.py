"""Phase 3 integration: analysis pipeline against real scan data in the DB.

Requires: a completed scan for the demo project (created in the live gate).
Runs inside the backend container.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _token(client) -> tuple[str, str]:
    import sqlalchemy as sa

    from app.db import SessionLocal
    from app.models import Project

    r = await client.post(
        "/api/auth/login",
        json={"email": "live@example.com", "password": "S3curePass!xyz"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]

    db = SessionLocal()
    try:
        project = db.execute(
            sa.select(Project).where(Project.base_url == "http://demo-app:9000")
        ).scalars().first()
        return token, project.id if project else ""
    finally:
        db.close()


@pytest.mark.skipif(True, reason="live-gate dependent: run after a scan exists (see PLAN.md docker gates)")
async def test_analyze_after_scan(client):
    token, project_id = await _token(client)
    if not project_id:
        pytest.skip("no demo project in DB")
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post(f"/api/projects/{project_id}/analyze", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pages_analyzed"] >= 1

    r = await client.get(f"/api/projects/{project_id}/analyze/apis", headers=headers)
    assert r.status_code == 200

    r = await client.get(f"/api/projects/{project_id}/analyze/architecture", headers=headers)
    assert r.status_code == 200
