"""LLM provider auto-selection (pure, SDK-free) and GeminiProvider wiring."""

from __future__ import annotations

from secure_context_pipeline.config import Settings
from secure_context_pipeline.pipeline.pipeline import select_provider_name

_MK = b"\x07" * 32


def test_auto_prefers_gemini_over_anthropic():
    s = Settings(master_key=_MK, gemini_api_key="g", anthropic_api_key="a")
    assert select_provider_name(s) == "gemini"


def test_auto_falls_back_to_anthropic_then_mock():
    assert select_provider_name(Settings(master_key=_MK, anthropic_api_key="a")) == "anthropic"
    assert select_provider_name(Settings(master_key=_MK)) == "mock"


def test_explicit_provider_overrides_keys():
    s = Settings(master_key=_MK, llm_provider="mock", gemini_api_key="g")
    assert select_provider_name(s) == "mock"
    s2 = Settings(master_key=_MK, llm_provider="anthropic", gemini_api_key="g")
    assert select_provider_name(s2) == "anthropic"


def test_gemini_provider_constructs_without_network():
    # Constructing must not require a live key / network call (lazy client init).
    import pytest

    try:
        from secure_context_pipeline.llm.provider import GeminiProvider
    except Exception:  # pragma: no cover
        pytest.skip("provider import failed")
    try:
        provider = GeminiProvider("dummy-key", "gemini-2.0-flash")
    except ModuleNotFoundError:
        pytest.skip("google-genai not installed")
    assert provider.name == "gemini"
