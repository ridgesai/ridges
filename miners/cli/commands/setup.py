"""Interactive `ridges miner setup` command."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from miners.cli.click_ext import click, format_help
from miners.cli.config import (
    MinerConfig,
    MinerConfigError,
    default_config_path,
    default_workspace,
    load_config,
    save_config,
)
from miners.cli.prompts import (
    prompt_confirm,
    prompt_select_provider,
    prompt_workspace,
    render_brand,
    render_setup_preview,
)
from miners.cli.provider_env import configured_provider_statuses

from .shared import console, print_provider_setup_hint, prompt_validated_agent_path


def _run_setup_flow(*, existing: MinerConfig, config_path: Path) -> MinerConfig:
    """Interactive first-run setup. Writes the resulting config to disk."""
    render_brand("local miner setup")
    console.print("Two quick questions. You can re-run this any time with `ridges miner setup`.\n")

    workspace = prompt_workspace(existing.workspace or default_workspace())
    agent_path = prompt_validated_agent_path(workspace=workspace, default=existing.agent_path)

    configured = configured_provider_statuses(workspace)
    provider = prompt_select_provider(configured, default=existing.provider) if configured else None

    candidate = replace(
        existing,
        workspace=workspace,
        agent_path=agent_path,
        provider=provider,
    )
    render_setup_preview(candidate)

    if not prompt_confirm("Save and continue?", default=True):
        console.print("[yellow]Setup cancelled.[/yellow]")
        raise click.exceptions.Exit(1)

    save_config(candidate, config_path)
    if not configured:
        print_provider_setup_hint(workspace)
    return candidate


@click.command(
    "setup",
    short_help="Create or update miner config.",
    help=format_help(
        "Create or update local miner config for interactive Harbor runs.",
        "ridges miner setup",
    ),
)
@click.option(
    "--config-path",
    default=None,
    help="Write the config to this path (default: ~/.config/ridges/miner.toml).",
)
def setup_command(config_path: str | None) -> None:
    target = Path(config_path) if config_path else default_config_path()
    try:
        existing = load_config(target, allow_legacy_inference_url=True)
    except MinerConfigError as exception:
        raise click.ClickException(f"Invalid miner config at {exception.path}: {exception}") from exception
    _run_setup_flow(existing=existing, config_path=target)
