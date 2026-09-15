"""Pluggable LLM layer (spec §1: never hard-code one provider).

AUTOQA_LLM_PROVIDER:
  - "none"    → deterministic heuristic fallbacks (no network)
  - "openai"  → any OpenAI-compatible API (OpenAI, vLLM, Ollama /v1, LM Studio)
"""
import json
from typing import Any

import httpx

from app.config import get_settings


class LLMError(RuntimeError):
    pass


def _call_openai_compatible(system: str, user: str) -> str:
    settings = get_settings()
    if not settings.llm_api_key and "api.openai.com" in settings.llm_base_url:
        raise LLMError("AUTOQA_LLM_API_KEY is required for hosted OpenAI")
    resp = httpx.post(
        f"{settings.llm_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json={
            "model": settings.llm_model,
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


def generate_json(system: str, user: str, fallback: Any) -> Any:
    """Ask the configured LLM for JSON; return fallback if provider is none
    or the call fails. Never raises for unconfigured environments."""
    settings = get_settings()
    if settings.llm_provider == "none":
        return fallback
    try:
        raw = _call_openai_compatible(system, user)
        return json.loads(raw)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, LLMError):
        return fallback
