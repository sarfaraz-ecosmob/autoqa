"""Pluggable LLM layer (spec §1: never hard-code one provider).

Providers:
  - "none"        → deterministic heuristic fallbacks (no network)
  - "openai"      → OpenAI + any self-hosted OpenAI-compatible gateway
                    (vLLM, Ollama /v1, LM Studio)
  - "openrouter"  → OpenRouter (https://openrouter.ai/api/v1) — hundreds of
                    models behind one key; OpenAI-compatible chat completions.

Configuration precedence (§22): the UI-managed singleton (llm_settings row,
API key Fernet-encrypted at rest) overrides environment variables when set.
.env remains the bootstrap fallback so fresh installs work without the UI.

Auto model selection (OpenRouter): with model="auto" (the default), the best
FREE model is detected automatically from OpenRouter's /models catalog —
zero prompt AND completion price, chat-capable, ranked by family strength
and context window — cached for 1 hour. Set an explicit slug to pin a model.

All providers share the same chat-completions wire format, so generate_json()
works identically across them. Every call degrades gracefully: on any failure
the caller's heuristic fallback is used and no exception escapes.
"""
import json
import time
from typing import Any

import httpx

from app.config import get_settings


class LLMError(RuntimeError):
    pass


# ---------- configuration (env + UI-managed DB override) ----------

def _db_overrides() -> dict[str, str]:
    """Read the UI-managed LlmSetting singleton. Empty dict on any problem —
    the layer must keep working (env fallback) even if the DB is unavailable."""
    try:
        from app.db import SessionLocal
        from app.models import LlmSetting

        db = SessionLocal()
        try:
            row = db.get(LlmSetting, 1)
            if row is None:
                return {}
            return {
                "provider": (row.provider or "").strip(),
                "model": (row.model or "").strip(),
                "base_url": (row.base_url or "").strip(),
                "api_key": "",  # resolved separately (decryption may fail)
                "has_db_key": bool((row.api_key_encrypted or "").strip()),
            }
        finally:
            db.close()
    except Exception:
        return {}


def _db_api_key() -> str:
    try:
        from app.db import SessionLocal
        from app.models import LlmSetting
        from app.security.crypto import decrypt_secret

        db = SessionLocal()
        try:
            row = db.get(LlmSetting, 1)
            if row is None or not (row.api_key_encrypted or "").strip():
                return ""
            return decrypt_secret(row.api_key_encrypted)
        finally:
            db.close()
    except Exception:
        return ""


def _effective_config() -> dict[str, str]:
    """Merge env config with UI-managed overrides (DB wins when set)."""
    s = get_settings()
    ov = _db_overrides()

    provider = ov.get("provider") or (s.llm_provider or "none")
    if not ov.get("has_db_key") and ov.get("provider") in ("", "none") and not (s.llm_api_key or "").strip():
        provider = "none"
    model = ov.get("model") or (s.llm_model or "auto")
    base_url = ov.get("base_url") or (s.llm_base_url or "")
    api_key = _db_api_key() if ov.get("has_db_key") else (s.llm_api_key or "")
    return {"provider": (provider or "none").lower().strip(), "model": model, "base_url": base_url, "api_key": api_key}


def _provider_config() -> tuple[str, str, dict[str, str]]:
    """Resolve (base_url, api_key, extra_headers) for the effective provider."""
    cfg = _effective_config()
    provider = cfg["provider"]

    if provider == "openrouter":
        base_url = cfg["base_url"].strip() or "https://openrouter.ai/api/v1"
        headers = {
            # OpenRouter attribution headers (optional but recommended)
            "HTTP-Referer": "https://github.com/autoqa",
            "X-Title": "AutoQA",
        }
        return base_url, cfg["api_key"], headers

    if provider == "openai":
        return (cfg["base_url"].strip() or "https://api.openai.com/v1"), cfg["api_key"], {}

    return "", "", {}


# ---------- free-model auto-detection (OpenRouter) ----------

_MODEL_CACHE: tuple[float, list[str]] | None = None  # (fetched_at, ranked model ids)
_MODEL_CACHE_TTL = 3600  # 1 hour
_FAILURE_CACHE: tuple[float, frozenset] | None = None  # (fetched_at, ids that failed with a non-429 error)
_FAILURE_CACHE_TTL = 900  # 15 minutes — dead/unavailable models stay deprioritized

# Strongest free families first (ordering is a heuristic, refreshed by context
# length within a family). Free-tier catalogs change weekly — that's why we
# detect instead of hard-code.
_FAMILY_PREFERENCE = (
    "deepseek", "qwen3", "qwen", "glm", "kimi", "gemini", "gpt", "claude", "llama", "mistral", "phi",
)
# Non-chat / non-text models must never be selected.
_BAD_ID_TOKENS = (
    "embed", "rerank", "whisper", "tts", "audio", "image", "diffusion",
    "dall", "flux", "veo", "sora", "guard", "moderation", "nemotron-vl",
    "lyria", "safety", "content-safety",
)
# Free models restricted to agentic harnesses / partner apps — they reject
# direct chat-completions calls with 403.
_HARNESS_ONLY_TOKENS = ("thinkingmachines/", "agentic", "harness")


def _is_free_model(m: dict) -> bool:
    """Free = zero prompt AND completion price (or legacy ':free' suffix), and
    a text-in/text-out chat model (never image/audio/music generators)."""
    arch = m.get("architecture") or {}
    out_mods = set(arch.get("output_modalities") or ["text"])
    in_mods = set(arch.get("input_modalities") or ["text"])
    if "text" not in out_mods or "text" not in in_mods:
        return False
    if out_mods - {"text"}:
        return False  # image/audio/video output → not a chat model
    pricing = m.get("pricing") or {}

    def _zero(v) -> bool:
        try:
            return float(v) == 0.0
        except (TypeError, ValueError):
            return False

    return (_zero(pricing.get("prompt")) and _zero(pricing.get("completion"))) or str(
        m.get("id", "")
    ).endswith(":free")


def _rank_free_model(m: dict) -> tuple:
    fid = str(m.get("id", "")).lower()
    family = next(
        (i for i, f in enumerate(_FAMILY_PREFERENCE) if f in fid), len(_FAMILY_PREFERENCE)
    )
    ctx = min(int(m.get("context_length") or 0), 2_000_000)
    return (family, -ctx)


def rank_free_models(models: list[dict], k: int = 5) -> list[str]:
    """Top-k free chat model ids from an OpenRouter /models payload."""
    failed: frozenset = frozenset()
    if _FAILURE_CACHE and time.time() - _FAILURE_CACHE[0] < _FAILURE_CACHE_TTL:
        failed = _FAILURE_CACHE[1]
    candidates = [
        m
        for m in models
        if m.get("id")
        and _is_free_model(m)
        and not any(b in str(m["id"]).lower() for b in _BAD_ID_TOKENS)
        and not any(b in str(m["id"]).lower() for b in _HARNESS_ONLY_TOKENS)
        and str(m["id"]) not in failed
    ]
    # Everything failed → fall back to the unfiltered ranked list rather than none.
    if not candidates:
        candidates = [
            m for m in models
            if m.get("id") and _is_free_model(m)
            and not any(b in str(m["id"]).lower() for b in _BAD_ID_TOKENS + _HARNESS_ONLY_TOKENS)
        ]
    return [m["id"] for m in sorted(candidates, key=_rank_free_model)[: max(1, k)]]


def pick_best_free_model(models: list[dict]) -> str:
    """Choose the best free chat model from an OpenRouter /models payload."""
    ranked = rank_free_models(models, k=1)
    if not ranked:
        raise LLMError("no free chat models available")
    return ranked[0]


def resolve_models(base_url: str, headers: dict[str, str], k: int = 3) -> list[str]:
    """Ranked model candidates: explicit slug, or auto-detected free models."""
    cfg = _effective_config()
    configured = (cfg["model"] or "").strip()
    if configured and configured.lower() != "auto":
        return [configured]

    provider = cfg["provider"]
    if provider != "openrouter":
        return [configured or "gpt-4o-mini"]  # hosted OpenAI sensible default

    global _MODEL_CACHE
    now = time.time()
    if _MODEL_CACHE and now - _MODEL_CACHE[0] < _MODEL_CACHE_TTL:
        return _MODEL_CACHE[1][:k]

    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=20)
        resp.raise_for_status()
        ranked = rank_free_models(resp.json().get("data", []), k=6)
        if not ranked:
            raise LLMError("no free chat models available")
        _MODEL_CACHE = (now, ranked)
        return ranked[:k]
    except (httpx.HTTPError, ValueError, LLMError):
        raise LLMError("could not auto-detect a free OpenRouter model") from None


def resolve_model(base_url: str, headers: dict[str, str]) -> str:
    """Single best model (compat wrapper)."""
    return resolve_models(base_url, headers, k=1)[0]


def detect_best_free_model() -> str | None:
    """Public helper for the settings UI: the model auto-selection would pick."""
    base_url, api_key, headers = _provider_config()
    if not base_url:
        return None
    if not api_key and "openrouter.ai" in base_url:
        return None
    try:
        return resolve_model(base_url, {**headers, "Authorization": f"Bearer {api_key}"})
    except LLMError:
        return None


# ---------- chat completions ----------

def _call_chat_completions(system: str, user: str) -> str:
    settings = get_settings()
    base_url, api_key, extra_headers = _provider_config()
    if not base_url:
        raise LLMError("LLM provider is not configured")

    # Hosted endpoints require a key; local gateways (vLLM/Ollama) may not.
    if not api_key and "openrouter.ai" in base_url:
        raise LLMError("an API key is required for OpenRouter (set it in Settings → AI Assistant)")
    if not api_key and "api.openai.com" in base_url:
        raise LLMError("an API key is required for hosted OpenAI (set it in Settings → AI Assistant)")

    headers = {"Authorization": f"Bearer {api_key}", **extra_headers}
    candidates = resolve_models(base_url, headers, k=3)
    if not candidates:
        raise LLMError("no LLM model configured")

    # Free-tier models are individually rate-limited: try each candidate in
    # rank order and rotate on rate-limit (429) or provider overflow errors.
    last_error: Exception | None = None
    for model in candidates:
        try:
            resp = httpx.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.2,
                },
                timeout=settings.llm_timeout_seconds,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            last_error = exc
            # Rotate on: rate limits (429), exhausted credits (402),
            # retired/paid-only free slugs (404), free-tier blocks (403),
            # and provider overflows (502/503). 401 = bad key → no point
            # rotating, the key is the same for every model.
            if exc.response.status_code == 401:
                raise
            if exc.response.status_code != 429:
                _record_model_failure(model)
            continue  # try the next candidate model
        except httpx.HTTPError as exc:
            last_error = exc
            continue
    raise LLMError(f"all candidate models failed: {str(last_error)[:200]}")


def _record_model_failure(model_id: str) -> None:
    """Cache non-429 failures so the ranker deprioritizes dead models for a
    while (e.g. ':free' slugs retired to paid-only, harness-restricted models)."""
    global _FAILURE_CACHE
    now = time.time()
    failed: set = set(_FAILURE_CACHE[1]) if _FAILURE_CACHE else set()
    failed.add(model_id)
    _FAILURE_CACHE = (now, frozenset(failed))


def generate_json(system: str, user: str, fallback: Any) -> Any:
    """Ask the effective LLM for JSON; return fallback if provider is none
    or the call fails. Never raises for unconfigured environments."""
    if _effective_config()["provider"] == "none":
        return fallback
    try:
        raw = _call_chat_completions(system, user)
        return json.loads(raw)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, LLMError, ValueError):
        return fallback


# ---------- object facade (used by the settings test endpoint) ----------

class _ChatLLM:
    model_name = "auto"

    def complete(self, prompt: str, max_tokens: int = 256) -> str:
        raw = _call_chat_completions("You are a helpful QA assistant.", prompt)
        return raw[:max_tokens]


class _HeuristicLLM:
    model_name = "heuristic"

    def complete(self, prompt: str, max_tokens: int = 256) -> str:
        return "ok (heuristic mode: no LLM provider configured)"


def get_llm():
    """LLM facade for simple completion calls; never fails to construct."""
    try:
        if _effective_config()["provider"] != "none":
            return _ChatLLM()
    except Exception:
        pass
    return _HeuristicLLM()
