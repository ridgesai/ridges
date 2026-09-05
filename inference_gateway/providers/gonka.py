from __future__ import annotations

import logging
from types import SimpleNamespace

from openai import APIStatusError, AsyncOpenAI

from inference_gateway import config
from inference_gateway.models import (
    EmbeddingModelInfo,
    EmbeddingResult,
    InferenceMessage,
    InferenceModelInfo,
    InferenceResult,
    InferenceTool,
    InferenceToolMode,
    inference_tool_mode_to_openai_tool_choice,
    inference_tools_to_openai_tools,
    openai_tool_calls_to_inference_tool_calls,
)
from inference_gateway.providers.provider import Provider

logger = logging.getLogger(__name__)

RIDGES_KIMI_MODEL_NAME = "moonshotai/Kimi-K2.6"


class GonkaProvider(Provider):
    async def init(self) -> GonkaProvider:
        self.name = "Gonka"
        self.inference_models.append(
            InferenceModelInfo(
                name=RIDGES_KIMI_MODEL_NAME,
                external_name=config.GONKA_KIMI_MODEL_ID,
                max_input_tokens=config.GONKA_CONTEXT_LENGTH,
                cost_usd_per_million_input_tokens=config.GONKA_COST_USD_PER_MILLION_TOKENS,
                cost_usd_per_million_output_tokens=config.GONKA_COST_USD_PER_MILLION_TOKENS,
            )
        )
        self.gonka_client = AsyncOpenAI(base_url=config.GONKA_BASE_URL, api_key=config.GONKA_API_KEY)
        return self

    async def _inference(
        self,
        *,
        model_info: InferenceModelInfo,
        temperature: float,
        messages: list[InferenceMessage],
        tool_mode: InferenceToolMode,
        tools: list[InferenceTool] | None,
    ) -> InferenceResult:
        try:
            completion_stream = await self.gonka_client.chat.completions.create(
                model=model_info.external_name,
                temperature=temperature,
                messages=messages,
                tool_choice=inference_tool_mode_to_openai_tool_choice(tool_mode),
                tools=inference_tools_to_openai_tools(tools) if tools else None,
                stream=True,
                stream_options={"include_usage": True},
            )
            streamed_completion: list[str] = []
            tool_calls: dict[int, SimpleNamespace] = {}
            usage = None

            async for chunk in completion_stream:
                if chunk.usage is not None:
                    usage = chunk.usage

                if not chunk.choices:
                    continue

                chunk_delta = chunk.choices[0].delta
                streamed_completion.append(chunk_delta.content or "")
                if chunk_delta.tool_calls is None:
                    continue

                for tool_call_chunk in chunk_delta.tool_calls:
                    if tool_call_chunk.index not in tool_calls:
                        tool_calls[tool_call_chunk.index] = SimpleNamespace(
                            id="",
                            type=tool_call_chunk.type,
                            function=SimpleNamespace(name="", arguments=""),
                        )
                    tool_call = tool_calls[tool_call_chunk.index]
                    if tool_call_chunk.id is not None:
                        tool_call.id += tool_call_chunk.id
                    if tool_call_chunk.function.name is not None:
                        tool_call.function.name += tool_call_chunk.function.name
                    if tool_call_chunk.function.arguments is not None:
                        tool_call.function.arguments += tool_call_chunk.function.arguments

            if usage is None:
                raise ValueError("Gonka response is missing token usage")

            message_tool_calls = [tool_calls[index] for index in sorted(tool_calls)]
            num_input_tokens = usage.prompt_tokens
            num_output_tokens = usage.completion_tokens
            return InferenceResult(
                status_code=200,
                content="".join(streamed_completion),
                tool_calls=openai_tool_calls_to_inference_tool_calls(message_tool_calls) if message_tool_calls else [],
                num_input_tokens=num_input_tokens,
                num_output_tokens=num_output_tokens,
                cost_usd=model_info.get_cost_usd(num_input_tokens, num_output_tokens),
            )

        except APIStatusError as error:
            return InferenceResult(status_code=error.status_code, error_message=error.response.text)
        except Exception as error:  # noqa: BLE001 - provider failures are returned through the gateway contract
            return InferenceResult(
                status_code=-1,
                error_message=f"Error in GonkaProvider._inference(): {type(error).__name__}: {error!s}",
            )

    async def _embedding(self, *, model_info: EmbeddingModelInfo, input: str) -> EmbeddingResult:
        return EmbeddingResult(status_code=405, error_message="Gonka does not support embeddings")
