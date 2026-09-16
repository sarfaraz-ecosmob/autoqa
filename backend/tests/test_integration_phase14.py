"""Phase 14 integration: settings/notifications + test-data/assistant API flow.

Runs against the live DB (like other integration phases); skips gracefully
when seed data (live gates) is absent.
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


# ------------------------------------------------------------ test data (§17)


async def test_environment_crud_masks_secrets(client):
    headers = await _auth_headers(client)
    project_id = await _project_id(client, headers)
    if not project_id:
        pytest.skip("no demo project in DB")

    # create
    r = await client.post(
        f"/api/projects/{project_id}/environments",
        headers=headers,
        json={"name": "it-env", "base_url": "http://demo-app:8000", "variables": {"user": "alice"}},
    )
    assert r.status_code == 201
    env = r.json()
    assert env["variables"]["user"] == "alice"

    # list shows it
    r = await client.get(f"/api/projects/{project_id}/environments", headers=headers)
    assert any(e["id"] == env["id"] for e in r.json()["items"])

    # dataset with a secret — masked on read, encrypted at rest
    r = await client.post(
        f"/api/projects/{project_id}/datasets",
        headers=headers,
        json={"name": "it-creds", "kind": "static", "values": {"password": "it-secret-123"}},
    )
    assert r.status_code == 201
    ds = r.json()
    assert ds["values"]["password"] == "••••••••"
    assert "it-secret-123" not in str(ds)
    assert "password" in ds["secret_keys"]

    # resolution preview masks the secret too
    r = await client.get(f"/api/projects/{project_id}/test-data/resolve", headers=headers)
    assert r.status_code == 200
    assert r.json()["variables"].get("password") == "••••••••"
    assert "it-secret-123" not in r.text

    # cleanup
    assert (await client.delete(f"/api/projects/{project_id}/datasets/{ds['id']}", headers=headers)).status_code == 204
    assert (await client.delete(f"/api/projects/{project_id}/environments/{env['id']}", headers=headers)).status_code == 204


async def test_dataset_patch_preserves_masked_secret(client):
    headers = await _auth_headers(client)
    project_id = await _project_id(client, headers)
    if not project_id:
        pytest.skip("no demo project in DB")

    r = await client.post(
        f"/api/projects/{project_id}/datasets",
        headers=headers,
        json={"name": "it-mask", "kind": "static", "values": {"token": "real-token"}},
    )
    ds = r.json()
    try:
        # PATCH echoing the masked placeholder must NOT overwrite the secret
        r2 = await client.patch(
            f"/api/projects/{project_id}/datasets/{ds['id']}",
            headers=headers,
            json={
                "name": "it-mask",
                "kind": "static",
                "values": {"token": "••••••••", "extra": "1"},
                "environment_id": None,
            },
        )
        assert r2.status_code == 200
        assert r2.json()["values"]["extra"] == "1"

        # the original secret still resolves (masked in the API response)
        r = await client.get(f"/api/projects/{project_id}/test-data/resolve", headers=headers)
        assert r.json()["variables"].get("token") == "••••••••"
        assert "real-token" not in r.text
    finally:
        await client.delete(f"/api/projects/{project_id}/datasets/{ds['id']}", headers=headers)


# ------------------------------------------------------------ assistant (§22)


async def test_assistant_answers_from_real_data(client):
    headers = await _auth_headers(client)
    project_id = await _project_id(client, headers)
    if not project_id:
        pytest.skip("no demo project in DB")

    r = await client.post(
        f"/api/projects/{project_id}/assistant/ask",
        headers=headers,
        json={"question": "Give me a QA summary"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["grounded"] is True
    assert body["intent"] == "summary"
    assert body["data"]["total_executions"] >= 1  # from real runs, not invented
    assert len(body["answer"]) > 20


async def test_assistant_unknown_project_403(client):
    headers = await _auth_headers(client)
    r = await client.post(
        "/api/projects/nonexistent/assistant/ask",
        headers=headers,
        json={"question": "summary"},
    )
    assert r.status_code in (403, 404)


async def test_assistant_requires_auth(client):
    r = await client.post("/api/projects/x/assistant/ask", json={"question": "hi"})
    assert r.status_code == 401


# ------------------------------------------------------------ settings (from earlier gate)


async def test_settings_flow(client):
    headers = await _auth_headers(client)
    project_id = await _project_id(client, headers)
    if not project_id:
        pytest.skip("no demo project in DB")

    # notifications inbox readable, unread count embedded
    r = await client.get("/api/settings/notifications", headers=headers)
    assert r.status_code == 200
    assert "items" in r.json() and "unread" in r.json()
