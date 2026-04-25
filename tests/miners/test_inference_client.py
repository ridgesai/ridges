import json

import pytest

from miners.inference_client import LocalInferenceClient, LocalInferenceConfig, LocalInferenceError


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def test_openrouter_inference_uses_chat_completions_shape(monkeypatch) -> None:
    captured = {}

    def fake_post(url, *, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "hello",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "search",
                                        "arguments": '{"query": "docs"}',
                                    }
                                }
                            ],
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("miners.inference_client.requests.post", fake_post)

    client = LocalInferenceClient(
        LocalInferenceConfig(provider="openrouter", api_key="secret", base_url="https://openrouter.ai/api/v1")
    )
    result = client.inference(
        model="moonshotai/Kimi-K2.5",
        temperature=0.0,
        messages=[{"role": "user", "content": "hi"}],
        tool_mode="required",
        tools=[
            {
                "name": "search",
                "description": "Look up docs",
                "parameters": [
                    {"name": "query", "type": "string", "description": "Query text", "required": True},
                ],
            }
        ],
        timeout=42,
    )

    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["json"]["tool_choice"] == "required"
    assert captured["json"]["tools"][0]["function"]["parameters"]["required"] == ["query"]
    assert result.content == "hello"
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments[0].name == "query"
    assert result.tool_calls[0].arguments[0].value == "docs"


def test_chutes_embedding_uses_embedding_base_url(monkeypatch) -> None:
    captured = {}

    def fake_post(url, *, json=None, headers=None, timeout=None):
        captured["url"] = url
        return FakeResponse({"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    monkeypatch.setattr("miners.inference_client.requests.post", fake_post)

    client = LocalInferenceClient(
        LocalInferenceConfig(
            provider="chutes",
            api_key="secret",
            base_url="https://llm.chutes.ai/v1",
            embedding_base_url="https://embed.chutes.ai/v1",
        )
    )

    embedding = client.embedding(model="Qwen/Qwen3-Embedding-8B", input="hello")

    assert captured["url"] == "https://embed.chutes.ai/v1/embeddings"
    assert embedding == [0.1, 0.2, 0.3]


def test_from_env_requires_provider(monkeypatch) -> None:
    monkeypatch.delenv("RIDGES_INFERENCE_PROVIDER", raising=False)
    monkeypatch.delenv("RIDGES_INFERENCE_API_KEY", raising=False)

    with pytest.raises(LocalInferenceError, match="RIDGES_INFERENCE_PROVIDER"):
        LocalInferenceClient.from_env()


def test_from_env_requires_complete_provider_config(monkeypatch) -> None:
    monkeypatch.setenv("RIDGES_INFERENCE_PROVIDER", "chutes")
    monkeypatch.setenv("RIDGES_INFERENCE_API_KEY", "secret")
    monkeypatch.setenv("RIDGES_INFERENCE_BASE_URL", "https://llm.chutes.ai/v1")
    monkeypatch.delenv("RIDGES_INFERENCE_EMBEDDING_BASE_URL", raising=False)

    with pytest.raises(LocalInferenceError, match="embedding_base_url"):
        LocalInferenceClient.from_env()
