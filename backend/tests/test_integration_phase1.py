"""Phase 1 integration tests: full API flow through the FastAPI app.

Runs inside the backend container with the real database (docker gate).
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register_and_login(client, email) -> dict:
    r = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "S3curePass!xyz", "name": "QA"},
    )
    assert r.status_code == 201, r.text
    r = await client.post(
        "/api/auth/login", json={"email": email, "password": "S3curePass!xyz"}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_register_login_me(client):
    tokens = await _register_and_login(client, "p1@example.com")

    r = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert r.status_code == 200
    assert r.json()["email"] == "p1@example.com"
    assert r.json()["role"] in {"admin", "tester"}


async def test_login_wrong_password_401(client):
    await _register_and_login(client, "p2@example.com")
    r = await client.post(
        "/api/auth/login", json={"email": "p2@example.com", "password": "WrongPass!123"}
    )
    assert r.status_code == 401


async def test_me_requires_token(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_refresh_flow(client):
    tokens = await _register_and_login(client, "p3@example.com")
    r = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    assert "access_token" in r.json()


async def test_project_crud_with_authorization_gate(client):
    tokens = await _register_and_login(client, "p4@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # SSRF-guarded creation
    r = await client.post(
        "/api/projects",
        headers=headers,
        json={"name": "Demo Shop QA", "base_url": "http://demo-app:9000"},
    )
    assert r.status_code == 422  # demo-app resolves privately inside compose

    r = await client.post(
        "/api/projects",
        headers=headers,
        json={"name": "Demo Shop QA", "base_url": "https://example.com"},
    )
    assert r.status_code == 201, r.text
    project_id = r.json()["id"]

    # get
    r = await client.get(f"/api/projects/{project_id}", headers=headers)
    assert r.status_code == 200

    # authorization confirmation (spec §2 step 4)
    r = await client.patch(
        f"/api/projects/{project_id}/authorization?confirmed=true", headers=headers
    )
    assert r.status_code == 200
    assert r.json()["authorization_confirmed"] is True

    # list includes it
    r = await client.get("/api/projects", headers=headers)
    assert any(p["id"] == project_id for p in r.json())


async def test_project_requires_auth(client):
    r = await client.get("/api/projects")
    assert r.status_code == 401


async def test_credentials_masked_in_response(client):
    tokens = await _register_and_login(client, "p5@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    r = await client.post(
        "/api/projects",
        headers=headers,
        json={
            "name": "Creds",
            "base_url": "https://example.com",
            "auth_type": "basic",
            "credentials": {"username": "alice", "password": "supersecret"},
        },
    )
    assert r.status_code == 201
    assert r.json()["credentials"]["password"] == "••••••••"
    assert r.json()["credentials"]["username"] == "alice"
