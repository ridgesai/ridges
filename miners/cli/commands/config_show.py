"""`ridges miner config` commands."""

from __future__ import annotations

from pathlib import Path

from miners.cli.click_ext import HELP_CONTEXT_SETTINGS, click, format_help
from miners.cli.config import default_config_path

from .shared import show_config


@click.group(
    "config",
    context_settings=HELP_CONTEXT_SETTINGS,
    invoke_without_command=True,
    short_help="Inspect saved miner config.",
    help=format_help(
        "Inspect saved miner config.",
        "ridges miner config show",
    ),
)
@click.pass_context
def config_group(ctx) -> None:
    """Inspect or manage miner config."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@config_group.command(
    "show",
    short_help="Print resolved miner config.",
    help=format_help(
        "Print resolved miner config and derived paths.",
        "ridges miner config show",
        "ridges miner config show --config-path /tmp/miner.toml",
    ),
)
@click.option("--config-path", default=None, help="Read config from this path.")
def config_show_command(config_path: str | None) -> None:
    show_config(Path(config_path) if config_path else default_config_path())
