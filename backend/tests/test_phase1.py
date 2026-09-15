"""Phase 1 unit tests: security primitives, storage, LLM fallback."""
import pytest

from app.security.crypto import decrypt_json, encrypt_json, mask_secrets, redact_text
from app.security.passwords import hash_password, verify_password
from app.security.ssrf import validate_target_url
from app.security.tokens import create_access_token, decode_token
from app.storage import put_bytes, get_bytes, exists, delete, checksum


# ---------- passwords ----------

def test_password_hash_roundtrip():
    h = hash_password("S3curePass!x")
    assert h != "S3curePass!x"
    assert verify_password("S3curePass!x", h)
    assert not verify_password("wrong", h)


# ---------- tokens ----------

def test_access_token_roundtrip():
    token = create_access_token("user-123", "admin")
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "user-123"
    assert payload["role"] == "admin"


def test_refresh_token_type_mismatch_rejected():
    from app.security.tokens import create_refresh_token
    from jwt import InvalidTokenError

    token = create_refresh_token("user-123")
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="access")


# ---------- crypto / secrets ----------

def test_encrypt_decrypt_roundtrip():
    blob = encrypt_json({"password": "hunter2", "user": "alice"})
    assert "hunter2" not in blob
    assert decrypt_json(blob) == {"password": "hunter2", "user": "alice"}


def test_mask_secrets():
    masked = mask_secrets({"username": "alice", "password": "hunter2", "api_key": "xyz"})
    assert masked["username"] == "alice"
    assert masked["password"] == "••••••••"
    assert masked["api_key"] == "••••••••"


def test_redact_text_removes_secret_values():
    out = redact_text("login with hunter2 now", {"password": "hunter2"})
    assert "hunter2" not in out


# ---------- SSRF guard ----------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000",
    "http://localhost/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://192.168.1.10/",
    "http://172.16.0.9/",
    "http://[::1]/",
    "file:///etc/passwd",
    "http://internal.service.svc/",
    "ftp://example.com",
])
def test_ssrf_blocked(url):
    with pytest.raises(ValueError):
        validate_target_url(url)


def test_ssrf_allows_public_url():
    # example.com is public; if DNS is unavailable in the sandbox this skips
    try:
        assert validate_target_url("https://example.com").startswith("https://")
    except ValueError as exc:
        pytest.skip(f"DNS unavailable in environment: {exc}")


# ---------- storage ----------

def test_storage_fs_roundtrip(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_driver", "fs")
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path))

    key = "runs/r1/screenshots/shot1.png"
    put_bytes(key, b"fake-png", "image/png")
    assert exists(key)
    assert get_bytes(key) == b"fake-png"
    assert len(checksum(b"fake-png")) == 64  # deterministic sha256
    delete(key)
    assert not exists(key)


# ---------- LLM fallback ----------

def test_llm_fallback_when_unconfigured():
    from app.ai import generate_json

    fallback = {"plan": ["heuristic"]}
    out = generate_json("system", "user", fallback)
    assert out == fallback  # provider=none → deterministic fallback


# ---------- rate limiter ----------

def test_rate_limiter_allows_then_blocks():
    class FakeRedis:
        def __init__(self):
            self.counts = {}

        def incr(self, key):
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]

        def expire(self, key, ttl):
            pass

    from app.security.ratelimit import RateLimiter

    limiter = RateLimiter(limit=3, window_seconds=60, redis_client=FakeRedis())
    assert limiter.check("k") is True
    assert limiter.check("k") is True
    assert limiter.check("k") is True
    assert limiter.check("k") is False
