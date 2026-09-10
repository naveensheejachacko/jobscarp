"""get_llm_provider() dispatch, including the DeepSeek provider added later —
DeepSeek's API is OpenAI-compatible, so DeepSeekProvider just points the
`openai` SDK at DeepSeek's base URL. No real network calls: constructing the
client doesn't hit the network, and _complete() is exercised against a fake
response object instead of a real API call."""
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.ai import AnthropicProvider, DeepSeekProvider, GeminiProvider, get_llm_provider


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_get_llm_provider_dispatches_gemini():
    provider = get_llm_provider(_settings(llm_provider="gemini", gemini_api_key="k", gemini_model="gemini-2.5-flash"))
    assert isinstance(provider, GeminiProvider)


def test_get_llm_provider_dispatches_anthropic():
    provider = get_llm_provider(_settings(llm_provider="anthropic", anthropic_api_key="k", anthropic_model="claude-opus-5"))
    assert isinstance(provider, AnthropicProvider)


def test_get_llm_provider_dispatches_deepseek():
    provider = get_llm_provider(_settings(llm_provider="deepseek", deepseek_api_key="k", deepseek_model="deepseek-chat"))
    assert isinstance(provider, DeepSeekProvider)


def test_get_llm_provider_rejects_unknown_provider():
    with pytest.raises(ValueError):
        get_llm_provider(_settings(llm_provider="not-a-real-provider"))


def test_deepseek_provider_uses_deepseek_base_url():
    provider = DeepSeekProvider(api_key="k", model="deepseek-chat")
    assert str(provider._client.base_url).rstrip("/") == "https://api.deepseek.com"


def test_deepseek_provider_complete_extracts_message_content(monkeypatch):
    provider = DeepSeekProvider(api_key="k", model="deepseek-chat")

    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hello from deepseek"))]
    )

    def fake_create(**kwargs):
        assert kwargs["model"] == "deepseek-chat"
        assert kwargs["messages"] == [{"role": "user", "content": "ping"}]
        return fake_response

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    assert provider._complete("ping") == "hello from deepseek"
