"""GeminiProvider tests use an injected fake client — no real API call, no
network, no API key required. If GEMINI_API_KEY happens to be unset, these
must still pass."""

import pytest

from repoimpact.llm import DEFAULT_GEMINI_MODEL, GeminiProvider, load_default_provider


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, response_text):
        self.response_text = response_text
        self.calls = []

    def generate_content(self, *, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return _FakeResponse(self.response_text)


class _FakeClient:
    def __init__(self, response_text="fake answer"):
        self.models = _FakeModels(response_text)


def test_explain_returns_response_text():
    fake_client = _FakeClient("Removing create_token would break login().")
    provider = GeminiProvider(client=fake_client, model="gemini-2.5-flash")

    answer = provider.explain(
        "What happens if I remove create_token?",
        {"symbol": {"qualified_name": "create_token"}, "impact": {"impact_level": "HIGH"}},
    )

    assert answer == "Removing create_token would break login()."


def test_explain_sends_model_and_compact_context_not_full_repo():
    fake_client = _FakeClient()
    provider = GeminiProvider(client=fake_client, model="gemini-2.5-flash")

    provider.explain(
        "What happens if I remove create_token?",
        {"symbol": {"qualified_name": "create_token"}, "impact": {"impact_level": "HIGH"}},
    )

    [call] = fake_client.models.calls
    assert call["model"] == "gemini-2.5-flash"
    assert "create_token" in call["contents"]
    assert "HIGH" in call["contents"]
    # The prompt is the question + a small JSON evidence blob, not source code.
    assert "def " not in call["contents"]
    assert len(call["contents"]) < 2000


def test_default_model_is_a_flash_class_model():
    assert "flash" in DEFAULT_GEMINI_MODEL


def test_missing_api_key_raises_without_network_call(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiProvider()


def test_load_default_provider_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert load_default_provider() is None


def test_load_default_provider_constructs_gemini_when_key_present(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-construction-only")
    provider = load_default_provider()
    assert isinstance(provider, GeminiProvider)
