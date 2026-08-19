from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType
from typing import Self

import pytest


@pytest.fixture
def openrouter_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    environment = {
        "HOST": "127.0.0.1",
        "PORT": "8000",
        "USE_DATABASE": "false",
        "MAX_COST_PER_EVALUATION_RUN_USD": "1",
        "USE_CHUTES": "false",
        "USE_TARGON": "false",
        "USE_OPENROUTER": "true",
        "OPENROUTER_BASE_URL": "https://openrouter.example/v1",
        "OPENROUTER_API_KEY": "test-key",
        "OPENROUTER_WEIGHT": "1",
        "TEST_INFERENCE_MODELS": "false",
        "TEST_EMBEDDING_MODELS": "false",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    sys.modules.pop("inference_gateway.providers.openrouter", None)
    sys.modules.pop("inference_gateway.config", None)
    return importlib.import_module("inference_gateway.providers.openrouter")


def test_deepseek_v4_flash_uses_exact_openrouter_slug(openrouter_module: ModuleType) -> None:
    model = next(
        model
        for model in openrouter_module.WHITELISTED_OPENROUTER_INFERENCE_MODELS
        if model.name == "deepseek-ai/DeepSeek-V4-Flash-0731"
    )

    assert model.openrouter_name == "deepseek/deepseek-v4-flash-0731"


def test_deepseek_v4_flash_metadata_comes_from_openrouter_catalog(
    monkeypatch: pytest.MonkeyPatch, openrouter_module: ModuleType
) -> None:
    inference_catalog = []
    for model in openrouter_module.WHITELISTED_OPENROUTER_INFERENCE_MODELS:
        is_deepseek_v4 = model.name == "deepseek-ai/DeepSeek-V4-Flash-0731"
        inference_catalog.append(
            {
                "id": model.openrouter_name,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "context_length": 1_048_576 if is_deepseek_v4 else 128_000,
                "pricing": {
                    "prompt": "0.00000014" if is_deepseek_v4 else "0.000001",
                    "completion": "0.00000028" if is_deepseek_v4 else "0.000002",
                },
            }
        )
    embedding_catalog = [
        {
            "id": model.openrouter_name,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["embeddings"]},
            "context_length": 32_768,
            "pricing": {"prompt": "0.00000001"},
        }
        for model in openrouter_module.WHITELISTED_OPENROUTER_EMBEDDING_MODELS
    ]

    class FakeResponse:
        def __init__(self, data: list[dict]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[dict]]:
            return {"data": self.data}

    class FakeAsyncClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str) -> FakeResponse:
            catalog = embedding_catalog if "/embeddings/" in url else inference_catalog
            return FakeResponse(catalog)

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(openrouter_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(openrouter_module, "AsyncOpenAI", FakeOpenAI)

    provider = asyncio.run(openrouter_module.OpenRouterProvider().init())
    model_info = provider.get_inference_model_info_by_name("deepseek-ai/DeepSeek-V4-Flash-0731")

    assert model_info is not None
    assert model_info.external_name == "deepseek/deepseek-v4-flash-0731"
    assert model_info.max_input_tokens == 1_048_576
    assert model_info.cost_usd_per_million_input_tokens == pytest.approx(0.14)
    assert model_info.cost_usd_per_million_output_tokens == pytest.approx(0.28)
