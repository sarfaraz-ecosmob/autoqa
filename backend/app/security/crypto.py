"""Secret encryption at rest + masking (spec §17, §25).

Credentials are encrypted with Fernet (AES-128-CBC + HMAC) keyed from
AUTOQA_SECRET_KEY. Secrets never appear in logs or API responses.
"""
import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_MASK = "••••••••"
_SECRET_KEYS = {"password", "token", "secret", "api_key", "authorization", "cookie"}


def _fernet() -> Fernet:
    key = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_json(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(blob: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(blob.encode()))
    except (InvalidToken, ValueError):
        return {}


def mask_secrets(data: dict) -> dict:
    """Return a copy with secret values masked — for API responses/UI."""
    masked = {}
    for k, v in data.items():
        if any(s in k.lower() for s in _SECRET_KEYS):
            masked[k] = _MASK
        elif isinstance(v, dict):
            masked[k] = mask_secrets(v)
        else:
            masked[k] = v
    return masked


def redact_text(text: str, secrets: dict) -> str:
    """Scrub known secret values out of free text (e.g. logs)."""
    out = text
    for v in secrets.values():
        if isinstance(v, str) and v:
            out = out.replace(v, _MASK)
    return out
