"""Local-only provider-direct inference helper for miner testing."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

SUPPORTED_INFERENCE_PROVIDERS = ("openrouter", "targon", "chutes")
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 150

LocalInferenceProvider = Literal["openrouter", "targon", "chutes"]


class _RequestsProxy:
    """Lazy requests loader so importing `miners` does not require runtime deps."""

    def __getattr__(self, name: str) -> Any:
        import requests as requests_module

        return getattr(requests_module, name)


requests = _RequestsProxy()


class LocalInferenceError(RuntimeError):
    """Raised when local provider-backed inference is misconfigured or fails."""


def _normalize_base_url(url: str, *, label: str) -> str:
    stripped = url.strip().rstrip("/")
    parsed = urlparse(stripped)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"{label} must include a scheme and host: {url}")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"{label} must not include params/query/fragment: {url}")
    return stripped


@dataclass(frozen=True, slots=True)
class ToolCallArgument:
    name: str
    value: Any


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: tuple[ToolCallArgument, ...]


@dataclass(frozen=True, slots=True)
class InferenceResult:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalInferenceConfig:
    """Resolved local inference provider settings for a single Harbor run."""

    provider: LocalInferenceProvider
    api_key: str
    base_url: str | None = None
    embedding_base_url: str | None = None

    def normalized(self) -> "LocalInferenceConfig":
        provider = self.provider.strip().lower()
        if provider not in SUPPORTED_INFERENCE_PROVIDERS:
            raise ValueError(
                f"Unsupported local inference provider {self.provider!r}; "
                f"expected one of {', '.join(SUPPORTED_INFERENCE_PROVIDERS)}"
            )

        api_key = self.api_key.strip()
        if not api_key:
            raise ValueError("Local inference api_key must not be empty")

        if provider == "openrouter":
            base_url = _normalize_base_url(self.base_url or DEFAULT_OPENROUTER_BASE_URL, label="OpenRouter base URL")
            embedding_base_url = (
                _normalize_base_url(self.embedding_base_url, label="OpenRouter embedding base URL")
                if self.embedding_base_url
                else base_url
            )
        elif provider == "targon":
            if not self.base_url:
                raise ValueError("Targon local inference requires a base_url")
            base_url = _normalize_base_url(self.base_url, label="Targon base URL")
            embedding_base_url = (
                _normalize_base_url(self.embedding_base_url, label="Targon embedding base URL")
                if self.embedding_base_url
                else base_url
            )
        else:
            if not self.base_url:
                raise ValueError("Chutes local inference requires an inference base_url")
            if not self.embedding_base_url:
                raise ValueError("Chutes local inference requires an embedding_base_url")
            base_url = _normalize_base_url(self.base_url, label="Chutes inference base URL")
            embedding_base_url = _normalize_base_url(self.embedding_base_url, label="Chutes embedding base URL")

        return LocalInferenceConfig(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            embedding_base_url=embedding_base_url,
        )

    def to_env(self) -> dict[str, str]:
        normalized = self.normalized()
        env = {
            "RIDGES_INFERENCE_PROVIDER": normalized.provider,
            "RIDGES_INFERENCE_API_KEY": normalized.api_key,
        }
        if normalized.base_url:
            env["RIDGES_INFERENCE_BASE_URL"] = normalized.base_url
        if normalized.embedding_base_url:
            env["RIDGES_INFERENCE_EMBEDDING_BASE_URL"] = normalized.embedding_base_url
        return env

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "LocalInferenceConfig":
        source = env if env is not None else os.environ
        provider = source.get("RIDGES_INFERENCE_PROVIDER")
        if not provider:
            raise LocalInferenceError(
                "Local inference is not configured. Set RIDGES_INFERENCE_PROVIDER and the matching "
                "RIDGES_INFERENCE_* variables before using LocalInferenceClient."
            )

        api_key = source.get("RIDGES_INFERENCE_API_KEY", "")
        if not api_key.strip():
            raise LocalInferenceError("RIDGES_INFERENCE_API_KEY is required for local provider inference")

        try:
            return cls(
                provider=provider,  # type: ignore[arg-type]
                api_key=api_key,
                base_url=source.get("RIDGES_INFERENCE_BASE_URL"),
                embedding_base_url=source.get("RIDGES_INFERENCE_EMBEDDING_BASE_URL"),
            ).normalized()
        except ValueError as exception:
            raise LocalInferenceError(str(exception)) from exception


def _tool_choice(tool_mode: str) -> str:
    normalized = (tool_mode or "none").strip().lower()
    if normalized not in {"none", "auto", "required"}:
        raise LocalInferenceError(f"Unsupported tool_mode {tool_mode!r}; expected one of none, auto, required")
    return normalized


def _tool_parameters_schema(parameters: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in parameters:
        name = str(parameter["name"])
        properties[name] = {
            "type": parameter.get("type", "string"),
            "description": parameter.get("description", ""),
        }
        if parameter.get("required"):
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def _openai_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None

    converted: list[dict[str, Any]] = []
    for tool in tools:
        if "input_schema" in tool:
            parameters = tool["input_schema"]
        else:
            parameters = _tool_parameters_schema(tool.get("parameters", []))
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": parameters,
                },
            }
        )
    return converted


def _parse_tool_calls(raw_tool_calls: list[dict[str, Any]] | None) -> tuple[ToolCall, ...]:
    if not raw_tool_calls:
        return ()

    parsed_calls: list[ToolCall] = []
    for raw_call in raw_tool_calls:
        function = raw_call.get("function", {})
        raw_arguments = function.get("arguments", "{}")
        try:
            argument_map = json.loads(raw_arguments)
        except json.JSONDecodeError:
            argument_map = {"__raw__": raw_arguments}
        parsed_calls.append(
            ToolCall(
                name=function.get("name", ""),
                arguments=tuple(ToolCallArgument(name=str(name), value=value) for name, value in argument_map.items()),
            )
        )
    return tuple(parsed_calls)


class LocalInferenceClient:
    """Small local-only client for direct provider-backed inference."""

    def __init__(self, config: LocalInferenceConfig) -> None:
        self.config = config.normalized()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "LocalInferenceClient":
        return cls(LocalInferenceConfig.from_env(env))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _timeout(self, timeout: int | float | None) -> tuple[int, float]:
        read_timeout = float(timeout) if timeout is not None else DEFAULT_REQUEST_TIMEOUT_SECONDS
        return (30, read_timeout)

    def _post(
        self, *, base_url: str, path: str, payload: dict[str, Any], timeout: int | float | None
    ) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=self._timeout(timeout))
            response.raise_for_status()
        except requests.HTTPError as exception:
            status_code = exception.response.status_code if exception.response is not None else "unknown"
            body = exception.response.text if exception.response is not None else str(exception)
            raise LocalInferenceError(f"Provider request failed with HTTP {status_code}: {body}") from exception
        except requests.RequestException as exception:
            raise LocalInferenceError(
                f"Provider request failed: {type(exception).__name__}: {exception}"
            ) from exception

        try:
            return response.json()
        except ValueError as exception:
            raise LocalInferenceError(f"Provider returned invalid JSON from {url}") from exception

    def inference(
        self,
        model: str,
        temperature: float,
        messages: list[dict[str, Any]],
        tool_mode: str = "none",
        tools: list[dict[str, Any]] | None = None,
        timeout: int | float | None = None,
    ) -> InferenceResult:
        payload: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "messages": messages,
            "tool_choice": _tool_choice(tool_mode),
        }
        openai_tools = _openai_tools(tools)
        if openai_tools is not None:
            payload["tools"] = openai_tools

        data = self._post(
            base_url=self.config.base_url or "",
            path="/chat/completions",
            payload=payload,
            timeout=timeout,
        )

        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exception:
            raise LocalInferenceError("Provider response did not contain choices[0].message") from exception

        return InferenceResult(
            content=message.get("content") or "",
            tool_calls=_parse_tool_calls(message.get("tool_calls")),
        )

    def embedding(self, model: str, input: str, timeout: int | float | None = None) -> list[float]:
        embedding_base_url = self.config.embedding_base_url or self.config.base_url
        if not embedding_base_url:
            raise LocalInferenceError("No embedding base URL configured for local provider inference")

        data = self._post(
            base_url=embedding_base_url,
            path="/embeddings",
            payload={"model": model, "input": input},
            timeout=timeout,
        )

        try:
            return data["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exception:
            raise LocalInferenceError("Provider embedding response did not contain data[0].embedding") from exception
