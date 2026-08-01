from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import APIStatusError

from inference_gateway.models import (
    InferenceMessage,
    InferenceTool,
    InferenceToolMode,
    InferenceToolParameter,
    InferenceToolParameterType,
)


@pytest.fixture
def gonka_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    environment = {
        "HOST": "127.0.0.1",
        "PORT": "8000",
        "USE_DATABASE": "false",
        "MAX_COST_PER_EVALUATION_RUN_USD": "1",
        "USE_CHUTES": "false",
        "USE_TARGON": "false",
        "USE_OPENROUTER": "false",
        "USE_GONKA": "true",
        "GONKA_BASE_URL": "https://gonka.example/v1",
        "GONKA_API_KEY": "test-key",
        "GONKA_WEIGHT": "2",
        "GONKA_COST_USD_PER_MILLION_TOKENS": "0.2",
        "TEST_INFERENCE_MODELS": "false",
        "TEST_EMBEDDING_MODELS": "false",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GONKA_KIMI_MODEL_ID", raising=False)
    monkeypatch.delenv("GONKA_CONTEXT_LENGTH", raising=False)

    for module_name in (
        "inference_gateway.main",
        "inference_gateway.providers.gonka",
        "inference_gateway.config",
    ):
        sys.modules.pop(module_name, None)
    return importlib.import_module("inference_gateway.providers.gonka")


class FakeStream:
    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self) -> FakeStream:
        return self

    async def __anext__(self) -> SimpleNamespace:
        try:
            return next(self._chunks)
        except StopIteration as exception:
            raise StopAsyncIteration from exception


class FakeCompletions:
    def __init__(self, *, chunks: list[SimpleNamespace] | None = None, error: Exception | None = None) -> None:
        self.chunks = chunks or []
        self.error = error
        self.kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> FakeStream:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return FakeStream(self.chunks)


class FakeClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _content_chunk(content: str | None, tool_calls: list[SimpleNamespace] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=None,
    )


def _usage_chunk(prompt_tokens: int = 1_000, completion_tokens: int = 250) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def _run_inference(provider: Any):
    return asyncio.run(
        provider.inference(
            model_name="moonshotai/Kimi-K2.6",
            temperature=0.0,
            messages=[InferenceMessage(role="user", content="search docs")],
            tool_mode=InferenceToolMode.REQUIRED,
            tools=[
                InferenceTool(
                    name="search",
                    description="Search documentation",
                    parameters=[
                        InferenceToolParameter(
                            name="query",
                            description="Search query",
                            type=InferenceToolParameterType.STRING,
                            required=True,
                        )
                    ],
                )
            ],
        )
    )


def test_init_exposes_only_kimi_k26_with_operator_pricing(
    monkeypatch: pytest.MonkeyPatch, gonka_module: ModuleType
) -> None:
    captured: dict[str, Any] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(gonka_module, "AsyncOpenAI", FakeOpenAI)

    provider = asyncio.run(gonka_module.GonkaProvider().init())

    assert provider.name == "Gonka"
    assert provider.embedding_models == []
    assert len(provider.inference_models) == 1
    model = provider.inference_models[0]
    assert model.name == "moonshotai/Kimi-K2.6"
    assert model.external_name == "moonshotai/kimi-k2.6"
    assert model.max_input_tokens == 240_000
    assert model.cost_usd_per_million_input_tokens == pytest.approx(0.2)
    assert model.cost_usd_per_million_output_tokens == pytest.approx(0.2)
    assert captured == {"base_url": "https://gonka.example/v1", "api_key": "test-key"}


def test_streaming_inference_assembles_content_tool_calls_usage_and_cost(
    monkeypatch: pytest.MonkeyPatch, gonka_module: ModuleType
) -> None:
    monkeypatch.setattr(gonka_module, "AsyncOpenAI", lambda **kwargs: object())
    provider = asyncio.run(gonka_module.GonkaProvider().init())
    first_tool_chunk = SimpleNamespace(
        index=0,
        id="call_1",
        type="function",
        function=SimpleNamespace(name="search", arguments='{"query":'),
    )
    second_tool_chunk = SimpleNamespace(
        index=0,
        id=None,
        type=None,
        function=SimpleNamespace(name=None, arguments='"docs"}'),
    )
    completions = FakeCompletions(
        chunks=[
            _content_chunk("Found ", [first_tool_chunk]),
            _content_chunk("it", [second_tool_chunk]),
            _usage_chunk(),
        ]
    )
    provider.gonka_client = FakeClient(completions)

    result = _run_inference(provider)

    assert result.status_code == 200
    assert result.content == "Found it"
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments[0].name == "query"
    assert result.tool_calls[0].arguments[0].value == "docs"
    assert result.num_input_tokens == 1_000
    assert result.num_output_tokens == 250
    assert result.cost_usd == pytest.approx(0.00025)
    assert completions.kwargs is not None
    assert completions.kwargs["model"] == "moonshotai/kimi-k2.6"
    assert completions.kwargs["stream"] is True
    assert completions.kwargs["stream_options"] == {"include_usage": True}


def test_streaming_inference_fails_closed_when_usage_is_missing(
    monkeypatch: pytest.MonkeyPatch, gonka_module: ModuleType
) -> None:
    monkeypatch.setattr(gonka_module, "AsyncOpenAI", lambda **kwargs: object())
    provider = asyncio.run(gonka_module.GonkaProvider().init())
    provider.gonka_client = FakeClient(FakeCompletions(chunks=[_content_chunk("unmetered")]))

    result = _run_inference(provider)

    assert result.status_code == -1
    assert "missing token usage" in result.error_message.lower()


def test_inference_preserves_upstream_http_status(monkeypatch: pytest.MonkeyPatch, gonka_module: ModuleType) -> None:
    monkeypatch.setattr(gonka_module, "AsyncOpenAI", lambda **kwargs: object())
    provider = asyncio.run(gonka_module.GonkaProvider().init())
    response = httpx.Response(
        429,
        text="rate limited",
        request=httpx.Request("POST", "https://gonka.example/v1/chat/completions"),
    )
    provider.gonka_client = FakeClient(
        FakeCompletions(error=APIStatusError("rate limited", response=response, body=None))
    )

    result = _run_inference(provider)

    assert result.status_code == 429
    assert "rate limited" in result.error_message


def test_embedding_is_explicitly_unsupported(monkeypatch: pytest.MonkeyPatch, gonka_module: ModuleType) -> None:
    monkeypatch.setattr(gonka_module, "AsyncOpenAI", lambda **kwargs: object())
    provider = asyncio.run(gonka_module.GonkaProvider().init())

    result = asyncio.run(provider._embedding(model_info=None, input="hello"))

    assert result.status_code == 405
    assert "does not support embeddings" in result.error_message


def test_gateway_registers_gonka_for_kimi_inference_only(
    monkeypatch: pytest.MonkeyPatch, gonka_module: ModuleType
) -> None:
    monkeypatch.setattr(gonka_module, "AsyncOpenAI", lambda **kwargs: object())
    gateway = importlib.import_module("inference_gateway.main")
    gateway.providers.clear()

    async def exercise_lifespan() -> None:
        async with gateway.lifespan(gateway.app):
            provider = gateway.get_provider_that_supports_model_for_inference("moonshotai/Kimi-K2.6")
            assert provider is not None
            assert provider.name == "Gonka"
            assert gateway.get_provider_that_supports_model_for_embedding("moonshotai/Kimi-K2.6") is None

    asyncio.run(exercise_lifespan())
