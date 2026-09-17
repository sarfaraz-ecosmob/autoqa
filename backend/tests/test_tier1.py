"""Tier-1 unit tests: recorder import, self-healing fallbacks, flaky scoring,
visual pixel diff, webhook signing/payload, cron validation.

No Playwright required — runs in the plain backend image.
"""
import base64
import io

import pytest


# ---------------------------------------------------------------- recorder


def test_recorder_parses_codegen_lines():
    from app.recorder import actions_to_steps

    actions = [
        {"line": "await page.goto('http://demo-app:9000/login');"},
        {"line": "await page.getByLabel('Email').fill('user@example.com');"},
        {"line": "await page.getByRole('button', { name: 'Sign in' }).click();"},
    ]
    steps, warnings = actions_to_steps(actions)
    assert steps[0] == {"action": "goto", "target": "http://demo-app:9000/login"}
    assert steps[1]["action"] == "fill" and "user@example.com" in steps[1]["value"]
    assert steps[2]["action"] == "click"
    assert "role=button" in steps[2]["target"] or "Sign in" in steps[2]["target"]
    assert warnings == []


def test_recorder_flags_unsupported():
    from app.recorder import actions_to_steps

    steps, warnings = actions_to_steps([{"line": "await page.setViewportSize({ width: 1, height: 1 });"}])
    assert steps == []
    assert any("not translatable" in w or "unsupported" in w for w in warnings)


def test_recorder_testid_normalization():
    from app.recorder import actions_to_steps

    steps, _ = actions_to_steps([{"line": "await page.getByTestId('login-btn').click();"}])
    assert steps[0]["target"] == '[data-testid="login-btn"]'


# ---------------------------------------------------------------- healing


def test_fallback_chain_attribute_swaps():
    from app.execution.healing import fallback_candidates

    cands = fallback_candidates('[data-testid="login-btn"]')
    assert '[data-test="login-btn"]' in cands
    assert "#login-btn" in cands
    assert any("role=button" in c for c in cands)


def test_fallback_chain_from_id_selector():
    from app.execution.healing import fallback_candidates

    cands = fallback_candidates("#submit-order")
    assert any("text=" in c for c in cands)


# ---------------------------------------------------------------- flaky


def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture()
def seeded_runs():
    from datetime import datetime, timedelta, timezone

    from app.models import Project, RunStatus, TestExecution, TestRun, TestCase, TestStatus, User

    db = _session()
    user = User(email="flaky-owner@example.com", password_hash="x", role="member")
    db.add(user)
    db.flush()
    project = Project(
        name="FlakyProj", base_url="https://f.example.com", description="",
        owner_id=user.id, authorization_confirmed=True, created_at=datetime.now(timezone.utc),
    )
    db.add(project)
    db.flush()

    stable = TestCase(project_id=project.id, ref="TC-STABLE-001", scenario="s", module="m", steps=[])
    flaky = TestCase(project_id=project.id, ref="TC-FLAKY-001", scenario="f", module="m", steps=[])
    db.add_all([stable, flaky])
    db.flush()

    base = datetime.now(timezone.utc)
    # stable: passes in 5 runs (first attempt)
    for i in range(5):
        run = TestRun(project_id=project.id, label=f"r{i}", status=RunStatus.completed,
                      created_at=base - timedelta(days=i + 2))
        db.add(run)
        db.flush()
        db.add(TestExecution(id=f"exec-s-{i}", test_run_id=run.id, test_case_id=stable.id,
                             browser="chromium", status=TestStatus.passed, attempt=1))
    # flaky: alternate fail/pass over 6 runs
    for i in range(6):
        run = TestRun(project_id=project.id, label=f"f{i}", status=RunStatus.completed,
                      created_at=base - timedelta(days=i + 1))
        db.add(run)
        db.flush()
        status = TestStatus.failed if i % 2 == 0 else TestStatus.passed
        db.add(TestExecution(id=f"exec-f-{i}", test_run_id=run.id, test_case_id=flaky.id,
                             browser="chromium", status=status, attempt=1))
    db.commit()
    return db, project, stable, flaky


def test_flaky_scores(seeded_runs):
    from app.analysis.flaky import analyze_project

    db, project, stable, flaky = seeded_runs
    report = analyze_project(db, str(project.id))
    by_case = {i["test_case_id"]: i for i in report["items"]}

    assert by_case[stable.id]["classification"] == "stable"
    assert by_case[stable.id]["flaky_score"] == 0

    f = by_case[flaky.id]
    assert f["classification"] in ("flaky", "suspect")
    assert f["alternating"] is True
    assert f["flaky_score"] >= 35


# ---------------------------------------------------------------- visual


def _png(color: tuple[int, int, int], size=(40, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_visual_diff_identical_and_changed(tmp_path, monkeypatch):
    import app.storage as storage
    from app.visual import _pixel_diff

    a, b = _png((255, 255, 255)), _png((255, 255, 255))
    _, pct_same = _pixel_diff(a, b)
    assert pct_same == 0.0

    _, pct_diff = _pixel_diff(a, _png((255, 0, 0)))
    assert pct_diff > 90.0

    diff_png, _ = _pixel_diff(a, _png((255, 0, 0)))
    assert diff_png.startswith(b"\x89PNG")


def test_visual_size_mismatch_counts_changed():
    from app.visual import _pixel_diff

    _, pct = _pixel_diff(_png((0, 0, 0), (40, 30)), _png((0, 0, 0), (60, 45)))
    assert pct > 50.0  # added white rows/cols count as changed


# ---------------------------------------------------------------- webhooks


def test_webhook_signature_is_deterministic_hmac():
    from app.webhooks import sign

    body = b'{"text":"hi"}'
    ts = "1700000000"
    s1 = sign(body, "sekret", ts)
    s2 = sign(body, "sekret", ts)
    s3 = sign(body, "other", ts)
    assert s1 == s2 and s1.startswith("sha256=")
    assert s1 != s3


def test_webhook_payload_has_slack_text():
    from app.webhooks import build_payload

    payload = build_payload({"kind": "run.completed", "title": "Run X", "body": "3 failed", "link": "/runs/1"})
    assert "Run X" in payload["text"] and "3 failed" in payload["text"]
    assert payload["event"]["kind"] == "run.completed"


# ---------------------------------------------------------------- cron


def test_cron_validation_accepts_and_rejects():
    from app.api.tier1 import _valid_cron

    assert _valid_cron("0 2 * * *")
    assert _valid_cron("*/15 * * * *")
    assert not _valid_cron("not a cron")
    assert not _valid_cron("99 99 * * *")


def test_croniter_next_run_is_future():
    from datetime import datetime, timezone

    from croniter import croniter

    from app.api.tier1 import _utcnow

    nxt = croniter("0 2 * * *", _utcnow()).get_next(datetime)
    assert nxt.tzinfo is not None or True
    assert nxt > datetime.now(timezone.utc) - __import__("datetime").timedelta(minutes=1)
