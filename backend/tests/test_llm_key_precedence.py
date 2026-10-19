"""LLM key resolution precedence: .env (container env) wins over the database.

Regression guard for the "the OpenRouter key lives in .env only" requirement:
an API key present in the environment is authoritative — provider/model and
the key all come from env, and the UI-managed (llm_settings) key is ignored.
The DB key is used only when .env has none, and provider "none" applies when
nothing is configured at all. Pure functions, no network.
"""
import pytest

from app.ai import _effective_config
from app.config import get_settings


@pytest.fixture()
def set_env(monkeypatch):
    """Configure the env side of the LLM settings (cached singleton)."""

    def _set(key="", provider="openrouter", model="auto", base_url=""):
        s = get_settings()
        monkeypatch.setattr(s, "llm_api_key", key)
        monkeypatch.setattr(s, "llm_provider", provider)
        monkeypatch.setattr(s, "llm_model", model)
        monkeypatch.setattr(s, "llm_base_url", base_url)

    return _set


def _patch_db(monkeypatch, *, ov: dict | None = None, key: str = "db-stored-key") -> None:
    """Simulate a UI-managed (database) key without touching a real DB."""
    from app import ai

    db_ov = {
        "provider": "openai",
        "model": "db-model",
        "base_url": "http://db-gateway:8000",
        "has_db_key": True,
    }
    monkeypatch.setattr(ai, "_db_overrides", lambda: dict(db_ov if ov is None else ov))
    monkeypatch.setattr(ai, "_db_api_key", lambda: key)


def test_env_key_wins_over_database_key(monkeypatch, set_env):
    _patch_db(monkeypatch)
    set_env(key="sk-or-v1-from-env", provider="openrouter")

    cfg = _effective_config()

    assert cfg["api_key"] == "sk-or-v1-from-env"
    assert cfg["provider"] == "openrouter"
    # env supplies the whole config — nothing leaks in from the DB row
    assert cfg["model"] == "auto"
    assert cfg["base_url"] == ""


def test_database_key_used_when_env_has_none(monkeypatch, set_env):
    _patch_db(monkeypatch)
    set_env(key="")

    cfg = _effective_config()

    assert cfg["api_key"] == "db-stored-key"
    assert cfg["provider"] == "openai"
    assert cfg["base_url"] == "http://db-gateway:8000"


def test_none_when_nothing_configured(monkeypatch, set_env):
    _patch_db(
        monkeypatch,
        ov={"provider": "none", "model": "", "base_url": "", "has_db_key": False},
        key="",
    )
    set_env(key="", provider="none")

    cfg = _effective_config()

    assert cfg["provider"] == "none"
    assert cfg["api_key"] == ""


def test_env_key_can_pin_a_model(monkeypatch, set_env):
    _patch_db(monkeypatch)
    set_env(key="sk-or-v1-x", provider="openrouter", model="z-ai/glm-5.2:free")

    cfg = _effective_config()

    assert (cfg["provider"], cfg["model"]) == ("openrouter", "z-ai/glm-5.2:free")


def test_whitespace_only_env_key_falls_back_to_db(monkeypatch, set_env):
    _patch_db(monkeypatch)
    set_env(key="   ")

    assert _effective_config()["api_key"] == "db-stored-key"


# ---------------------------------------------------------- settings API surface


def test_settings_api_reports_env_source_and_masks_env_key(monkeypatch, set_env):
    """GET /settings/ai must report the EFFECTIVE source (env wins) and mask
    the key actually in use — not the ignored DB one."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app import ai
    from app.api import settings as settings_router
    from app.api.deps import get_current_user, get_db
    from app.models import Base, LlmSetting, Role, User
    from app.security.crypto import encrypt_secret

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    user = User(email="llm-prec-test@example.com", password_hash="x", role=Role.admin)
    db.add(user)
    # A DB-stored key that must be IGNORED while the env key is present.
    db.add(
        LlmSetting(
            id=1,
            provider="openai",
            model="db-model",
            api_key_encrypted=encrypt_secret("db-stored-key"),
        )
    )
    db.commit()

    app = FastAPI()
    app.include_router(settings_router.router)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: user

    # no network in unit tests: stub the free-model catalog lookup
    monkeypatch.setattr(ai, "detect_best_free_model", lambda *a, **k: "stub/free-model")
    set_env(key="sk-or-v1-from-env", provider="openrouter")

    res = TestClient(app).get("/api/settings/ai").json()

    assert res["source"] == "env"
    assert res["env_override"] is True
    assert res["provider"] == "openrouter"
    assert res["has_key"] is True
    # masked preview is of the env key, never its plaintext
    assert res["masked_key"] and "sk-or-v1-from-env" not in res["masked_key"]
    assert res["detected_free_model"] == "stub/free-model"


def test_settings_api_reports_database_source_without_env_key(monkeypatch, set_env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app import ai
    from app.api import settings as settings_router
    from app.api.deps import get_current_user, get_db
    from app.models import Base, LlmSetting, Role, User
    from app.security.crypto import encrypt_secret

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    user = User(email="llm-prec-db@example.com", password_hash="x", role=Role.admin)
    db.add(user)
    db.add(
        LlmSetting(
            id=1,
            provider="openrouter",
            model="auto",
            api_key_encrypted=encrypt_secret("db-stored-key"),
        )
    )
    db.commit()

    app = FastAPI()
    app.include_router(settings_router.router)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(ai, "detect_best_free_model", lambda *a, **k: "stub/free-model")
    set_env(key="", provider="openrouter")

    res = TestClient(app).get("/api/settings/ai").json()

    assert res["source"] == "database"
    assert res["env_override"] is False
    assert res["has_key"] is True
    assert res["masked_key"] and "db-stored-key" not in res["masked_key"]
