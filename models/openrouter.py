"""Shared runtime models for OpenRouter-backed evaluations."""

from __future__ import annotations

from pydantic import BaseModel


class OpenRouterRuntimeConfig(BaseModel):
    """Fields needed to run OpenRouter-backed evaluations."""

    api_key: str
    management_key: str
    workspace_id: str
    expected_api_key_sha256: str

    def agent_env_vars(self) -> dict[str, str]:
        """Return the agent-visible env vars for OpenRouter inference."""
        return {
            "OPENROUTER_API_KEY": self.api_key,
        }

    def sidecar_env_vars(self) -> dict[str, str]:
        """Return the sidecar-visible env vars for OpenRouter policy enforcement."""
        return {
            "RIDGES_OPENROUTER_MANAGEMENT_KEY": self.management_key,
            "RIDGES_OPENROUTER_WORKSPACE_ID": self.workspace_id,
            "RIDGES_OPENROUTER_EXPECTED_API_KEY_SHA256": self.expected_api_key_sha256,
        }
