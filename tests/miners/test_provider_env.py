from pathlib import Path

from miners.cli.provider_env import (
    configured_provider_statuses,
    load_provider_env,
    missing_provider_message,
    provider_statuses,
    resolve_inference_config,
    workspace_env_path,
)
from miners.local_harbor import CustomSandboxProxyConfig


def test_process_env_overrides_workspace_env(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    workspace_env_path(workspace).write_text(
        "RIDGES_OPENROUTER_API_KEY=file-key\nRIDGES_OPENROUTER_BASE_URL=https://file.example/v1\n"
    )
    monkeypatch.setenv("RIDGES_OPENROUTER_API_KEY", "env-key")

    loaded = load_provider_env(workspace)

    assert loaded["RIDGES_OPENROUTER_API_KEY"] == "env-key"
    assert loaded["RIDGES_OPENROUTER_BASE_URL"] == "https://file.example/v1"


def test_openrouter_default_base_url_is_applied(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    workspace_env_path(workspace).write_text("RIDGES_OPENROUTER_API_KEY=test-key\n")

    status = provider_statuses(workspace)["openrouter"]

    assert status.configured is True
    assert status.base_url == "https://openrouter.ai/api/v1"


def test_provider_statuses_track_configured_vs_incomplete(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    workspace_env_path(workspace).write_text(
        "\n".join(
            [
                "RIDGES_OPENROUTER_API_KEY=openrouter-key",
                "RIDGES_TARGON_API_KEY=targon-key",
                "RIDGES_CHUTES_API_KEY=chutes-key",
                "RIDGES_CHUTES_INFERENCE_BASE_URL=https://llm.chutes.ai/v1",
            ]
        )
    )

    statuses = provider_statuses(workspace)

    assert statuses["openrouter"].configured is True
    assert statuses["targon"].configured is False
    assert statuses["targon"].missing_vars == ("RIDGES_TARGON_BASE_URL",)
    assert statuses["chutes"].configured is False
    assert statuses["chutes"].missing_vars == ("RIDGES_CHUTES_EMBEDDING_BASE_URL",)
    assert statuses["custom"].configured is False
    assert statuses["custom"].missing_vars == ("RIDGES_CUSTOM_SANDBOX_PROXY_URL",)


def test_resolve_inference_config_reads_provider_specific_env(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    workspace_env_path(workspace).write_text(
        "\n".join(
            [
                "RIDGES_CHUTES_API_KEY=chutes-key",
                "RIDGES_CHUTES_INFERENCE_BASE_URL=https://llm.chutes.ai/v1",
                "RIDGES_CHUTES_EMBEDDING_BASE_URL=https://embeddings.chutes.ai/v1",
            ]
        )
    )

    config = resolve_inference_config("chutes", workspace)

    assert config.provider == "chutes"
    assert config.api_key == "chutes-key"
    assert config.base_url == "https://llm.chutes.ai/v1"
    assert config.embedding_base_url == "https://embeddings.chutes.ai/v1"


def test_resolve_inference_config_reads_custom_proxy_env(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    workspace_env_path(workspace).write_text("RIDGES_CUSTOM_SANDBOX_PROXY_URL=https://proxy.example\n")

    config = resolve_inference_config("custom", workspace)

    assert isinstance(config, CustomSandboxProxyConfig)
    assert config.sandbox_proxy_url == "https://proxy.example"


def test_configured_provider_statuses_returns_only_ready_providers(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    workspace_env_path(workspace).write_text(
        "\n".join(
            [
                "RIDGES_OPENROUTER_API_KEY=openrouter-key",
                "RIDGES_TARGON_API_KEY=targon-key",
            ]
        )
    )

    configured = configured_provider_statuses(workspace)

    assert [status.provider for status in configured] == ["openrouter"]


def test_missing_provider_message_for_custom_mentions_proxy_contract(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    message = missing_provider_message("custom", workspace)

    assert "RIDGES_CUSTOM_SANDBOX_PROXY_URL" in message
    assert "/api/inference" in message
    assert "/api/embedding" in message
