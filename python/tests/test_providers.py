"""模型供應商清單與預設模型的測試。"""

import pytest

from harness import llm
from harness import providers

KEY_VARS = [item.key_var for item in providers.PROVIDERS if item.key_var]


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    """清掉所有跟模型連線有關的環境變數。"""
    for name in (*KEY_VARS, "HARNESS_MODEL", "HARNESS_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


def test_default_model_falls_back_without_any_key():
    assert llm.default_model() == llm.FALLBACK_MODEL


def test_default_model_follows_the_configured_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert llm.default_model() == "openai/gpt-5"


def test_default_model_prefers_earlier_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    assert llm.default_model() == "anthropic/claude-opus-5-5"


def test_harness_model_wins_over_detection(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("HARNESS_MODEL", "my-model")

    assert llm.default_model() == "my-model"


def test_endpoint_mode_does_not_guess_from_provider_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("HARNESS_BASE_URL", "http://localhost:4000")

    assert llm.default_model() == llm.FALLBACK_MODEL


@pytest.mark.parametrize(
    ("model", "provider_id"),
    [
        ("anthropic/claude-opus-5-5", "anthropic"),
        ("ollama_chat/hermes3", "ollama"),
        ("ollama/hermes3", "ollama"),
        ("groq/llama", None),
        ("claude", None),
    ],
)
def test_of_model_matches_by_prefix(model, provider_id):
    found = providers.of_model(model)

    assert (found.id if found else None) == provider_id


def test_is_ready_needs_the_key_of_the_model_provider(monkeypatch):
    assert providers.is_ready("anthropic/claude-opus-5-5") is False

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert providers.is_ready("anthropic/claude-opus-5-5") is False
    assert providers.is_ready("openai/gpt-5") is True


def test_is_ready_without_key_for_local_and_unknown_providers(monkeypatch):
    assert providers.is_ready("ollama_chat/hermes3") is True
    assert providers.is_ready("groq/llama") is True

    monkeypatch.setenv("HARNESS_BASE_URL", "http://localhost:4000")
    assert providers.is_ready("anthropic/claude-opus-5-5") is True
