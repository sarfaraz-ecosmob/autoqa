"""Live E2E: upload a CSV with priority/tags columns, verify import + generation.

Run inside the backend container: python /app/scripts/live_csv_upload_check.py
Uses the seeded demo user + project. Cleans up after itself.
"""
import csv
import io
import json
import urllib.request

BASE = "http://localhost:8000"
EMAIL = "live@example.com"
PASSWORD = "S3curePass!xyz"

SAMPLE_ROWS = [
    {
        "id": "REQ-LIVE-001",
        "title": "User can log in with valid credentials",
        "description": "Valid credentials reach the dashboard; wrong password shows a handled error.",
        "priority": "high",
        "tags": "auth;smoke",
    },
    {
        "id": "REQ-LIVE-002",
        "title": "User can add a product to the cart",
        "description": "Add to cart increments the badge; max quantity 10 is enforced.",
        "priority": "P1",
        "tags": "cart | ui",
    },
    {
        "id": "REQ-LIVE-003",
        "title": "Password reset via email link",
        "description": "Reset link expires after 30 minutes and cannot be reused.",
        "priority": "URGENT",
        "tags": "reset|email",
    },
]


def req(method: str, path: str, token: str | None = None, data: dict | None = None,
        raw: bytes | None = None, content_type: str | None = None) -> tuple[int, dict | bytes]:
    url = f"{BASE}{path}"
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    r = urllib.request.Request(url, data=body, method=method)
    if content_type:
        r.add_header("Content-Type", content_type)
    elif body is not None:
        r.add_header("Content-Type", "application/json")  # JSON bodies default to JSON
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r) as resp:
            payload = resp.read()
            return resp.status, (json.loads(payload) if payload[:1] in (b"{", b"[") else payload)
    except urllib.error.HTTPError as e:
        payload = e.read()
        return e.code, (json.loads(payload) if payload[:1] in (b"{", b"[") else payload)


def main() -> int:
    # 1. login
    status, out = req("POST", "/api/auth/login", data={"email": EMAIL, "password": PASSWORD})
    assert status == 200, f"login failed: {status} {out}"
    token = out["access_token"]
    print("[1] login OK")

    # 2. find demo project
    status, projects = req("GET", "/api/projects", token=token)
    assert status == 200, f"projects failed: {status}"
    project = next((p for p in projects if p["name"] == "Demo Scan"), None)
    assert project, "Demo Scan project not found"
    pid = project["id"]
    print(f"[2] found project {pid}")

    # pre-check: clean slate for our IDs
    existing = req("GET", f"/api/projects/{pid}/requirements", token=token)[1]
    for r in existing:
        if r["external_id"].startswith("REQ-LIVE-"):
            req("DELETE", f"/api/projects/{pid}/requirements/{r['id']}", token=token)
    print("[3] cleaned any prior REQ-LIVE-* rows")

    # 4. build the CSV and upload it
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["id", "title", "description", "priority", "tags"])
    writer.writeheader()
    writer.writerows(SAMPLE_ROWS)
    csv_bytes = buf.getvalue().encode()

    boundary = "----livecsvcheck"
    parts = [
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="live-sample.csv"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ]
    body = parts[0].encode() + csv_bytes + f"\r\n--{boundary}--\r\n".encode()
    status, out = req(
        "POST", f"/api/projects/{pid}/requirements/import-file", token=token,
        raw=body, content_type=f"multipart/form-data; boundary={boundary}",
    )
    assert status == 201, f"import failed: {status} {out}"
    assert out["imported"] == 3, f"expected 3 imported, got {out}"
    print(f"[4] uploaded live-sample.csv → imported {out['imported']} (skipped {out['skipped']})")

    # 5. verify priorities & tags landed correctly
    status, req_list = req("GET", f"/api/projects/{pid}/requirements", token=token)
    assert status == 200, f"list requirements failed: {status}"
    reqs = {r["external_id"]: r for r in req_list}
    checks = [
        ("REQ-LIVE-001", "high", ["auth", "smoke"]),
        ("REQ-LIVE-002", "high", ["cart", "ui"]),     # P1 → high
        ("REQ-LIVE-003", "critical", ["reset", "email"]),  # URGENT → critical
    ]
    for ext, want_prio, want_tags in checks:
        row = reqs[ext]
        assert row["priority"] == want_prio, f"{ext}: priority {row['priority']} != {want_prio}"
        assert sorted(row["tags"]) == sorted(want_tags), f"{ext}: tags {row['tags']} != {want_tags}"
        print(f"[5] {ext}: priority={row['priority']} tags={row['tags']} OK")

    # 6. generate cases and check priority inheritance
    status, out = req(
        "POST", f"/api/projects/{pid}/requirements/generate-cases", token=token,
        data={"requirement_ids": [reqs[e]["id"] for e, _, _ in checks], "use_llm": False},
    )
    assert status == 200, f"generate failed: {status} {out}"
    assert out["generated"] >= 15, f"expected >=15 cases, got {out['generated']}"
    print(f"[6] generated {out['generated']} cases (skipped {out['skipped']} existing)")

    cases = req("GET", f"/api/projects/{pid}/test-cases", token=token)[1]
    items = cases if isinstance(cases, list) else cases.get("items", cases.get("cases", []))
    for ext, want_prio, _ in checks:
        mine = [c for c in items if c["ref"].startswith(f"TC-{ext[4:]}")]
        assert mine, f"no cases found for {ext}"
        assert all(c["priority"] == want_prio for c in mine), \
            f"{ext}: case priorities {[c['priority'] for c in mine]} != {want_prio}"
        print(f"[7] {ext} → {len(mine)} cases, all priority={want_prio} OK")

    # 8. re-upload idempotency: all skipped
    body = parts[0].encode() + csv_bytes + f"\r\n--{boundary}--\r\n".encode()
    status, out = req(
        "POST", f"/api/projects/{pid}/requirements/import-file", token=token,
        raw=body, content_type=f"multipart/form-data; boundary={boundary}",
    )
    assert status == 409, f"expected 409 all-duplicates, got {status} {out}"
    print(f"[8] re-upload correctly rejected as duplicates: {out['detail'][:60]}…")

    # 9. cleanup
    for ext, _, _ in checks:
        req("DELETE", f"/api/projects/{pid}/requirements/{reqs[ext]['id']}", token=token)
    print("[9] cleaned up REQ-LIVE-* requirements and their generated cases")

    print("\nALL LIVE CHECKS PASSED ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
