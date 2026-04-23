"""`ridges miner` group and subcommand registration."""

from __future__ import annotations

from miners.cli.click_ext import HELP_CONTEXT_SETTINGS, click, format_help

from .cleanup import cleanup_command
from .config_show import config_group
from .run_local import run_local_command
from .setup import setup_command


@click.group(
    context_settings=HELP_CONTEXT_SETTINGS,
    invoke_without_command=True,
    short_help="Local miner testing and test Harbor runs.",
    help=format_help(
        "Local miner testing, provider setup, and task runs.",
        "ridges miner setup",
        "ridges miner run-local",
        "ridges miner run-local --task-path ./task --provider custom",
        "ridges miner cleanup --dry-run",
    ),
)
@click.pass_context
def miner(ctx) -> None:
    """Miner-facing local testing and config."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


miner.add_command(setup_command)
miner.add_command(config_group)
miner.add_command(cleanup_command)
miner.add_command(run_local_command)
