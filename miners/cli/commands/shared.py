"""Shared helpers for miner CLI commands."""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console

from miners.cli.agent_file import discover_agent_candidates, validate_agent_file
from miners.cli.click_ext import click
from miners.cli.config import (
    MinerConfig,
    MinerConfigError,
    apply_overrides,
    default_config_path,
    load_config,
)
from miners.cli.prompts import (
    prompt_agent_candidate,
    prompt_agent_path,
    prompt_confirm,
    render_config_card,
)
from miners.cli.provider_env import example_env_path, workspace_env_path

console = Console()
RECOMMENDED_DATASETS: tuple[tuple[str, str], ...] = (
    (
        "aider-polyglot@1.0",
        "Multi-language programming exercises across ~10 languages.",
    ),
    (
        "swebench-verified@1.0",
        "Human-validated subset of 500 SWE-bench tasks (long-horizon software engineering).",
    ),
)


def _load_config_or_exit(path: Path) -> MinerConfig:
    try:
        return load_config(path)
    except MinerConfigError as exception:
        raise click.ClickException(f"Invalid miner config at {exception.path}: {exception}") from exception


def _ensure_workspace_env_file(workspace: Path) -> tuple[Path, bool]:
    env_path = workspace_env_path(workspace)
    if env_path.exists():
        return env_path, False

    env_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example_env_path(), env_path)
    return env_path, True


def print_provider_setup_hint(workspace: Path) -> None:
    try:
        env_path, created = _ensure_workspace_env_file(workspace)
    except OSError as exception:
        console.print(
            "[yellow]No configured local providers found.[/yellow] "
            f"Could not create {workspace_env_path(workspace)} automatically ({exception}). "
            f"Copy {example_env_path()} there and fill one of: OpenRouter, Targon, Chutes, or Custom."
        )
        return

    if created:
        console.print(
            f"[yellow]Created {env_path} for you.[/yellow] "
            "Fill in one provider section (OpenRouter, Targon, Chutes, or Custom), "
            "then run `ridges miner run-local` again."
        )
        return

    console.print(
        f"[yellow]No configured local providers found in {env_path}.[/yellow] "
        "Fill in one provider section (OpenRouter, Targon, Chutes, or Custom), "
        "then run `ridges miner run-local` again."
    )


def prompt_validated_agent_path(*, workspace: Path, default: Path | None) -> Path | None:
    """Guide the user toward a valid agent path without importing user code."""
    candidates = discover_agent_candidates(Path.cwd(), workspace)
    if default is not None and default.exists():
        resolved_default = default.resolve()
        if resolved_default not in candidates:
            candidates = [resolved_default, *candidates]

    while True:
        if candidates:
            selection = prompt_agent_candidate(candidates, default=default)
            if selection == "__skip__":
                return default if default and validate_agent_file(default).ok else None
            candidate = prompt_agent_path(default) if selection == "__other__" else Path(selection).expanduser()
        else:
            candidate = prompt_agent_path(default)

        validation = validate_agent_file(candidate)
        if validation.ok:
            return Path(candidate).expanduser().resolve()

        console.print(f"[yellow]{validation.message}[/yellow]")
        if not prompt_confirm("Try another agent file?", default=True):
            return default if default and validate_agent_file(default).ok else None


def show_config(config_path: Path) -> None:
    cfg = _load_config_or_exit(config_path)
    render_config_card(cfg, config_path=config_path)


def resolve_effective_results_dir(
    *,
    config_path: Path | None,
    workspace: Path | None,
    results_dir: Path | None,
) -> Path:
    config_target = config_path or default_config_path()
    cfg = _load_config_or_exit(config_target)
    cfg = apply_overrides(cfg, workspace=workspace)
    return results_dir or cfg.results_dir
