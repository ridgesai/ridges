"""Tests for inference_gateway/models.py — model definitions, tool conversion, and cost calculations."""

import json
import pytest

from inference_gateway.models import (
    InferenceModelInfo,
    EmbeddingModelInfo,
    EmbeddingModelPricingMode,
    InferenceToolCall,
    InferenceToolCallArgument,
    InferenceToolParameter,
    InferenceToolParameterType,
    InferenceTool,
    InferenceToolMode,
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    inference_tool_parameters_to_openai_parameters,
    inference_tools_to_openai_tools,
    inference_tool_mode_to_openai_tool_choice,
    openai_tool_calls_to_inference_tool_calls,
)
from uuid import uuid4


class TestInferenceModelInfo:
    """Tests for InferenceModelInfo cost calculation."""

    def test_cost_calculation_basic(self):
        model = InferenceModelInfo(
            name="test-model",
            external_name="ext-test",
            max_input_tokens=4096,
            cost_usd_per_million_input_tokens=1.0,
            cost_usd_per_million_output_tokens=2.0,
        )
        cost = model.get_cost_usd(num_input_tokens=1_000_000, num_output_tokens=1_000_000)
        assert cost == 3.0

    def test_cost_calculation_zero_tokens(self):
        model = InferenceModelInfo(
            name="test", external_name="test",
            max_input_tokens=4096,
            cost_usd_per_million_input_tokens=10.0,
            cost_usd_per_million_output_tokens=20.0,
        )
        assert model.get_cost_usd(0, 0) == 0.0

    def test_cost_calculation_fractional(self):
        model = InferenceModelInfo(
            name="test", external_name="test",
            max_input_tokens=4096,
            cost_usd_per_million_input_tokens=3.0,
            cost_usd_per_million_output_tokens=6.0,
        )
        cost = model.get_cost_usd(500_000, 250_000)
        assert abs(cost - 3.0) < 1e-10  # 1.5 + 1.5


class TestEmbeddingModelInfo:
    """Tests for EmbeddingModelInfo cost calculation."""

    def test_per_token_pricing(self):
        model = EmbeddingModelInfo(
            name="embed", external_name="embed",
            max_input_tokens=8192,
            pricing_mode=EmbeddingModelPricingMode.PER_TOKEN,
            cost_usd_per_million_input_tokens=0.1,
        )
        cost = model.get_cost_usd(num_input_tokens=2_000_000, num_seconds=0)
        assert abs(cost - 0.2) < 1e-10

    def test_per_second_pricing(self):
        model = EmbeddingModelInfo(
            name="embed", external_name="embed",
            max_input_tokens=8192,
            pricing_mode=EmbeddingModelPricingMode.PER_SECOND,
            cost_usd_per_second=0.001,
        )
        cost = model.get_cost_usd(num_input_tokens=0, num_seconds=60)
        assert abs(cost - 0.06) < 1e-10

    def test_default_pricing_mode_is_per_token(self):
        model = EmbeddingModelInfo(
            name="embed", external_name="embed",
            max_input_tokens=8192,
            cost_usd_per_million_input_tokens=1.0,
        )
        assert model.pricing_mode == EmbeddingModelPricingMode.PER_TOKEN


class TestInferenceToolConversions:
    """Tests for tool parameter and tool conversion functions."""

    def test_parameters_to_openai_format(self):
        params = [
            InferenceToolParameter(
                type=InferenceToolParameterType.STRING,
                name="query",
                description="Search query",
                required=True,
            ),
            InferenceToolParameter(
                type=InferenceToolParameterType.INTEGER,
                name="limit",
                description="Max results",
                required=False,
            ),
        ]
        result = inference_tool_parameters_to_openai_parameters(params)
        assert "query" in result["properties"]
        assert result["properties"]["query"]["type"] == "string"
        assert "query" in result["required"]
        assert "limit" not in result["required"]

    def test_tools_to_openai_tools(self):
        tools = [
            InferenceTool(
                name="search",
                description="Search the web",
                parameters=[
                    InferenceToolParameter(
                        type=InferenceToolParameterType.STRING,
                        name="q",
                        description="Query",
                        required=True,
                    )
                ],
            )
        ]
        openai_tools = inference_tools_to_openai_tools(tools)
        assert len(openai_tools) == 1
        assert openai_tools[0]["type"] == "function"
        assert openai_tools[0]["function"]["name"] == "search"

    def test_tool_mode_none_to_openai(self):
        assert inference_tool_mode_to_openai_tool_choice(InferenceToolMode.NONE) == "none"

    def test_tool_mode_auto_to_openai(self):
        assert inference_tool_mode_to_openai_tool_choice(InferenceToolMode.AUTO) == "auto"

    def test_tool_mode_required_to_openai(self):
        assert inference_tool_mode_to_openai_tool_choice(InferenceToolMode.REQUIRED) == "required"

    def test_empty_tools_list_conversion(self):
        assert inference_tools_to_openai_tools([]) == []


class TestInferenceToolCallArgument:
    """Tests for InferenceToolCallArgument and InferenceToolCall."""

    def test_tool_call_argument_creation(self):
        arg = InferenceToolCallArgument(name="param1", value="value1")
        assert arg.name == "param1"
        assert arg.value == "value1"

    def test_tool_call_with_multiple_args(self):
        tc = InferenceToolCall(
            name="my_tool",
            arguments=[
                InferenceToolCallArgument(name="a", value=1),
                InferenceToolCallArgument(name="b", value="hello"),
            ],
        )
        assert tc.name == "my_tool"
        assert len(tc.arguments) == 2


class TestInferenceRequestResponse:
    """Tests for request/response model validation."""

    def test_inference_request_creation(self):
        req = InferenceRequest(
            evaluation_run_id=uuid4(),
            model="gpt-4",
            temperature=0.7,
            messages=[InferenceMessage(role="user", content="Hello")],
        )
        assert req.model == "gpt-4"
        assert len(req.messages) == 1

    def test_inference_request_default_tool_mode(self):
        req = InferenceRequest(
            evaluation_run_id=uuid4(),
            model="gpt-4",
            temperature=0.0,
            messages=[InferenceMessage(role="user", content="Hi")],
        )
        assert req.tool_mode == InferenceToolMode.NONE

    def test_inference_response_creation(self):
        resp = InferenceResponse(content="Hello!", tool_calls=[])
        assert resp.content == "Hello!"
        assert resp.tool_calls == []

    def test_embedding_request_creation(self):
        req = EmbeddingRequest(
            evaluation_run_id=uuid4(),
            model="text-embedding-ada-002",
            input="Hello world",
        )
        assert req.input == "Hello world"

    def test_embedding_response_creation(self):
        resp = EmbeddingResponse(embedding=[0.1, 0.2, 0.3])
        assert len(resp.embedding) == 3


class TestAllToolParameterTypes:
    """Ensure all parameter types are valid."""

    def test_all_parameter_types_exist(self):
        expected = {"boolean", "integer", "number", "string", "array", "object"}
        actual = {t.value for t in InferenceToolParameterType}
        assert actual == expected

    def test_each_type_converts_to_openai(self):
        for ptype in InferenceToolParameterType:
            params = [
                InferenceToolParameter(
                    type=ptype, name="test", description="test", required=True
                )
            ]
            result = inference_tool_parameters_to_openai_parameters(params)
            assert result["properties"]["test"]["type"] == ptype.value
