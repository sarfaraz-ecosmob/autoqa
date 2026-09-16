"""Phase 14 unit tests: test data generators, encryption, masking, resolution.

DB-dependent resolution tests use a SQLite in-memory session built from the
real models (no Playwright needed — runs in the plain backend image).
"""
import pytest

from app.testdata import (
    decrypt_dataset_values,
    encrypt_dataset_values,
    generate_value,
    mask_dataset_values,
)


def test_generated_values_are_deterministic_per_seed():
    a = generate_value("user", {}, seed=42)
    b = generate_value("user", {}, seed=42)
    c = generate_value("user", {}, seed=43)
    assert a == b
    assert a != c
    assert "." in a["username"] and "@" in a["email"]


def test_generated_value_types():
    assert "@" in generate_value("email", {}, seed=1)
    assert len(generate_value("uuid", {}, seed=1)) == 36
    assert str(generate_value("int", {}, seed=1)).isdigit()
    assert len(generate_value("string", {}, seed=1)) >= 8


def test_encrypt_roundtrip_masks_on_output():
    stored, encrypted_keys = encrypt_dataset_values({"password": "s3cret", "env": "staging"})
    assert "password" in encrypted_keys
    # ciphertext must not contain the plaintext
    assert "s3cret" not in str(stored)
    # decryption restores plaintext
    plain = decrypt_dataset_values(stored, encrypted_keys)
    assert plain["password"] == "s3cret"
    assert plain["env"] == "staging"
    # masking never leaks secrets
    masked = mask_dataset_values(stored)
    assert masked["password"] == "••••••••"
    assert "s3cret" not in str(masked)


def test_mask_leaves_public_values_visible():
    masked = mask_dataset_values({"username": "qa_bot"})
    assert masked["username"] == "qa_bot"


def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture()
def seeded_project():
    """A project + user + env + datasets for resolution tests."""
    from datetime import datetime, timezone

    from app.models import Project, TestDataset, TestEnvironment, User

    db = _session()
    user = User(email="td-owner@example.com", password_hash="x", role="member")
    db.add(user)
    db.flush()
    project = Project(
        name="TD",
        base_url="https://td.example.com",
        description="",
        owner_id=user.id,
        authorization_confirmed=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(project)
    db.flush()
    env = TestEnvironment(
        project_id=project.id,
        name="staging",
        base_url="https://staging.example.com",
        variables={"base_user": "staging_alice"},
    )
    db.add(env)
    db.flush()
    secret_stored, secret_keys = encrypt_dataset_values({"password": "s3cret"})
    db.add_all(
        [
            TestDataset(
                project_id=project.id,
                name="creds",
                kind="static",
                values=secret_stored,
                encrypted_keys=secret_keys,
                is_active=True,
            ),
            TestDataset(
                project_id=project.id,
                name="gen",
                kind="generated",
                generator="email",
                values={},
                is_active=True,
            ),
            TestDataset(
                project_id=project.id,
                name="off",
                kind="static",
                values={"unused": "x"},
                is_active=False,
            ),
        ]
    )
    db.commit()
    return db, project, env


def test_resolution_layers_and_secrets(seeded_project):
    from app.testdata import resolve_variables

    db, project, env = seeded_project
    variables = resolve_variables(project, env.id, seed=7, db=db)

    # env var layer present
    assert variables["base_user"] == "staging_alice"
    # static dataset secret decrypted for execution only
    assert variables["password"] == "s3cret"
    # generated dataset synthesized deterministically (dataset key comes from
    # its values mapping; "gen" dataset uses generator=email with values {})
    assert isinstance(variables["gen"], str) and variables["gen"]
    # inactive dataset excluded
    assert "unused" not in variables


def test_resolution_without_env_skips_env_scoped_datasets(seeded_project):
    from app.models import TestEnvironment
    from app.testdata import resolve_variables

    db, project, env = seeded_project

    # Exactly one environment → it is the implicit default
    variables = resolve_variables(project, None, seed=7, db=db)
    assert variables["base_user"] == "staging_alice"
    assert variables["password"] == "s3cret"

    # Two environments → no silent default; env vars must not leak across targets
    db.add(TestEnvironment(project_id=project.id, name="prod-like", base_url="", variables={"prod_only": "1"}))
    db.commit()
    variables = resolve_variables(project, None, seed=7, db=db)
    assert "base_user" not in variables
    assert "prod_only" not in variables
    # global datasets still apply
    assert variables["password"] == "s3cret"
