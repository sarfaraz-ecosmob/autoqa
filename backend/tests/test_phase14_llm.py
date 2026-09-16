"""Phase 14 unit tests: LLM adapter — OpenRouter free-model auto-detection.

Pure-function tests (no network). The assistant grounding tests live in
test_phase14.py; these cover model selection logic only.
"""
from app.ai import pick_best_free_model
from app.ai import LLMError


def _model(fid: str, prompt: str = "0", completion: str = "0", ctx: int = 8192) -> dict:
    return {
        "id": fid,
        "context_length": ctx,
        "pricing": {"prompt": prompt, "completion": completion},
    }


def test_picks_free_model_only():
    models = [
        _model("vendor/paid-model", prompt="0.000001", completion="0.000002"),
        _model("deepseek/deepseek-chat-v3:free"),
    ]
    assert pick_best_free_model(models) == "deepseek/deepseek-chat-v3:free"


def test_prefers_stronger_family():
    models = [
        _model("meta-llama/llama-3-8b:free", ctx=1000000),
        _model("deepseek/deepseek-r1:free", ctx=64000),
    ]
    # deepseek family outranks llama regardless of context window
    assert pick_best_free_model(models) == "deepseek/deepseek-r1:free"


def test_context_length_breaks_family_ties():
    models = [
        _model("qwen/qwen3-8b:free", ctx=32000),
        _model("qwen/qwen3-235b:free", ctx=131072),
    ]
    assert pick_best_free_model(models) == "qwen/qwen3-235b:free"


def test_excludes_non_chat_models():
    models = [
        _model("vendor/text-embedding-3:free", ctx=8192),
        _model("vendor/flux-image:free", ctx=0),
        _model("openai/gpt-4o-mini:free"),
    ]
    assert pick_best_free_model(models) == "openai/gpt-4o-mini:free"


def test_zero_pricing_counts_as_free_without_suffix():
    models = [
        _model("z-ai/glm-4.5-air"),  # no :free suffix but zero-priced
        _model("mistral/mistral-7b:free"),
    ]
    # glm ranks above mistral in family preference
    assert pick_best_free_model(models) == "z-ai/glm-4.5-air"


def test_no_free_models_raises():
    import pytest

    with pytest.raises(LLMError):
        pick_best_free_model([_model("vendor/paid", prompt="0.01", completion="0.02")])


def test_generate_json_falls_back_on_provider_none():
    from app.ai import generate_json

    assert generate_json("sys", "user", {"answer": "heuristic"}) == {"answer": "heuristic"}
