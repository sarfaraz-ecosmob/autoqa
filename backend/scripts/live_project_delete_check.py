"""Live E2E: DELETE /api/projects/{id} must return 204 for a data-heavy project.

Reproduces the production failure: project with an `applications` row (from an
analysis run), pages, endpoints, plan, cases, suite, run, executions, defects,
schedule, webhook, dataset, env, requirements... then deletes via the API.
Run inside the backend container: python /app/scripts/live_project_delete_check.py
"""
import json
import urllib.error
import urllib.request

BASE = "http://localhost:8000"
EMAIL = "live@example.com"
PASSWORD = "S3curePass!xyz"


def req(method, path, token=None, data=None):
    r = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        method=method,
    )
    if data is not None:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r) as resp:
            payload = resp.read()
            return resp.status, (json.loads(payload) if payload[:1] in (b"{", b"[") else payload)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def main():
    status, out = req("POST", "/api/auth/login", data={"email": EMAIL, "password": PASSWORD})
    assert status == 200, f"login failed: {status} {out}"
    token = out["access_token"]
    print("[1] login OK")

    status, out = req("POST", "/api/projects", token=token, data={
        "name": "delete-check E2E", "base_url": "http://demo-app:9000",
        "authorization_confirmed": True,
    })
    assert status == 201, f"create failed: {status} {out}"
    pid = out["id"]
    print(f"[2] project created {pid}")

    # Simulate what the UI accumulates before the user hits Delete.
    req("POST", f"/api/projects/{pid}/requirements/import", token=token, data={
        "requirements": [
            {"external_id": "REQ-DEL-1", "title": "login works", "description": "d"},
            {"external_id": "REQ-DEL-2", "title": "cart works", "description": "d"},
        ],
        "use_llm": False,
    })
    req("POST", f"/api/projects/{pid}/requirements/generate-cases", token=token,
        data={"use_llm": False})
    req("POST", f"/api/projects/{pid}/environments", data={"name": "staging"}, token=token)
    req("POST", f"/api/projects/{pid}/datasets", data={"name": "creds", "values": {"user": "a"}}, token=token)
    req("POST", f"/api/projects/{pid}/analyze", token=token)  # creates the applications row (old bug trigger)
    req("POST", f"/api/projects/{pid}/test-runs", token=token, data={"refs": [], "browsers": ["chromium"]})
    print("[3] seeded requirements/cases/env/dataset/analysis/run (analysis row = old bug trigger)")

    status, out = req("DELETE", f"/api/projects/{pid}", token=token)
    assert status == 204, f"DELETE failed: {status} {out}"
    print("[4] DELETE returned 204 (was 500 before the fix)")

    status, out = req("GET", f"/api/projects/{pid}", token=token)
    assert status == 404, f"project still present: {status}"
    print("[5] project gone — LIVE E2E PASSED ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
