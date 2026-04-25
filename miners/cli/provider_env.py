"""Workspace-scoped provider environment loading for local miner runs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from miners.inference_client import DEFAULT_OPENROUTER_BASE_URL, LocalInferenceConfig
from miners.local_harbor import CustomSandboxProxyConfig, LocalRunInferenceConfig

ENV_FILENAME = ".env.miner"
EXAMPLE_ENV_FILENAME = "env.miner.example"
PROVIDER_LABELS = {
    "openrouter": "OpenRouter",
    "targon": "Targon",
    "chutes": "Chutes",
    "custom": "Custom (advanced)",
}


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    provider: str
    configured: bool
    missing_vars: tuple[str, ...]
    base_url: str | None = None
    embedding_base_url: str | None = None
    sandbox_proxy_url: str | None = None

    @property
    def label(self) -> str:
        return PROVIDER_LABELS.get(self.provider, self.provider)

    @property
    def detail(self) -> str:
        if self.configured:
            return "configured"
        if not self.missing_vars:
            return "not configured"
        if len(self.missing_vars) == 1:
            return f"missing {self.missing_vars[0]}"
        return "missing " + ", ".join(self.missing_vars)


def workspace_env_path(workspace: Path) -> Path:
    return workspace / ENV_FILENAME


def example_env_path() -> Path:
    return Path(__file__).resolve().parents[1] / EXAMPLE_ENV_FILENAME


def load_provider_env(workspace: Path) -> dict[str, str]:
    from dotenv import dotenv_values

    merged: dict[str, str] = {}
    env_path = workspace_env_path(workspace)
    if env_path.exists():
        merged.update({key: value for key, value in dotenv_values(env_path).items() if value is not None})
    merged.update(os.environ)
    return merged


def _value(env: dict[str, str], key: str) -> str | None:
    raw = env.get(key)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def provider_statuses(workspace: Path) -> dict[str, ProviderStatus]:
    env = load_provider_env(workspace)

    openrouter_api_key = _value(env, "RIDGES_OPENROUTER_API_KEY")
    openrouter_base_url = _value(env, "RIDGES_OPENROUTER_BASE_URL") or DEFAULT_OPENROUTER_BASE_URL

    targon_api_key = _value(env, "RIDGES_TARGON_API_KEY")
    targon_base_url = _value(env, "RIDGES_TARGON_BASE_URL")

    chutes_api_key = _value(env, "RIDGES_CHUTES_API_KEY")
    chutes_inference_base_url = _value(env, "RIDGES_CHUTES_INFERENCE_BASE_URL")
    chutes_embedding_base_url = _value(env, "RIDGES_CHUTES_EMBEDDING_BASE_URL")
    custom_sandbox_proxy_url = _value(env, "RIDGES_CUSTOM_SANDBOX_PROXY_URL")

    return {
        "openrouter": ProviderStatus(
            provider="openrouter",
            configured=openrouter_api_key is not None,
            missing_vars=() if openrouter_api_key is not None else ("RIDGES_OPENROUTER_API_KEY",),
            base_url=openrouter_base_url,
            embedding_base_url=openrouter_base_url,
        ),
        "targon": ProviderStatus(
            provider="targon",
            configured=targon_api_key is not None and targon_base_url is not None,
            missing_vars=tuple(
                key
                for key, value in (
                    ("RIDGES_TARGON_API_KEY", targon_api_key),
                    ("RIDGES_TARGON_BASE_URL", targon_base_url),
                )
                if value is None
            ),
            base_url=targon_base_url,
            embedding_base_url=targon_base_url,
        ),
        "chutes": ProviderStatus(
            provider="chutes",
            configured=(
                chutes_api_key is not None
                and chutes_inference_base_url is not None
                and chutes_embedding_base_url is not None
            ),
            missing_vars=tuple(
                key
                for key, value in (
                    ("RIDGES_CHUTES_API_KEY", chutes_api_key),
                    ("RIDGES_CHUTES_INFERENCE_BASE_URL", chutes_inference_base_url),
                    ("RIDGES_CHUTES_EMBEDDING_BASE_URL", chutes_embedding_base_url),
                )
                if value is None
            ),
            base_url=chutes_inference_base_url,
            embedding_base_url=chutes_embedding_base_url,
        ),
        "custom": ProviderStatus(
            provider="custom",
            configured=custom_sandbox_proxy_url is not None,
            missing_vars=() if custom_sandbox_proxy_url is not None else ("RIDGES_CUSTOM_SANDBOX_PROXY_URL",),
            sandbox_proxy_url=custom_sandbox_proxy_url,
        ),
    }


def configured_provider_statuses(workspace: Path) -> list[ProviderStatus]:
    return [status for status in provider_statuses(workspace).values() if status.configured]


def resolve_inference_config(provider: str, workspace: Path) -> LocalRunInferenceConfig:
    statuses = provider_statuses(workspace)
    status = statuses.get(provider)
    if status is None:
        raise RuntimeError(f"Unsupported local provider {provider!r}")
    if not status.configured:
        raise RuntimeError(missing_provider_message(provider, workspace))

    env = load_provider_env(workspace)
    if provider == "openrouter":
        return LocalInferenceConfig(
            provider="openrouter",
            api_key=_value(env, "RIDGES_OPENROUTER_API_KEY") or "",
            base_url=status.base_url,
        ).normalized()
    if provider == "targon":
        return LocalInferenceConfig(
            provider="targon",
            api_key=_value(env, "RIDGES_TARGON_API_KEY") or "",
            base_url=status.base_url,
        ).normalized()
    if provider == "chutes":
        return LocalInferenceConfig(
            provider="chutes",
            api_key=_value(env, "RIDGES_CHUTES_API_KEY") or "",
            base_url=status.base_url,
            embedding_base_url=status.embedding_base_url,
        ).normalized()
    if provider == "custom":
        return CustomSandboxProxyConfig(
            sandbox_proxy_url=_value(env, "RIDGES_CUSTOM_SANDBOX_PROXY_URL") or "",
        ).normalized()

    raise RuntimeError(f"Unsupported local provider {provider!r}")


def missing_provider_message(provider: str | None, workspace: Path) -> str:
    statuses = provider_statuses(workspace)
    env_path = workspace_env_path(workspace)
    if provider is None:
        return (
            f"No local inference provider is configured. Fill {env_path} from {example_env_path()} "
            "or export the matching RIDGES_* environment variables."
        )

    status = statuses.get(provider)
    if status is None:
        return f"Unsupported local provider {provider!r}"
    missing = ", ".join(status.missing_vars) or "the required provider variables"
    if provider == "custom":
        return (
            f"Provider {status.label} is not configured. Missing {missing}. See {env_path}. "
            "The custom endpoint must implement /api/inference and /api/embedding."
        )
    return f"Provider {status.label} is not configured. Missing {missing}. See {env_path}."
