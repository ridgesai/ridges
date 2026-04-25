"""Top-level Ridges CLI composition."""

from __future__ import annotations

from dotenv import load_dotenv

from miners.cli.click_ext import HELP_CONTEXT_SETTINGS, click, format_help
from miners.cli.commands import miner
from miners.cli.commands.upload import DEFAULT_API_BASE_URL, upload

load_dotenv(".env")


@click.group(
    context_settings=HELP_CONTEXT_SETTINGS,
    invoke_without_command=True,
    help=format_help(
        "Manage Ridges uploads and local miner workflows.",
        "ridges upload --file agent.py",
        "ridges miner setup",
        "ridges miner run-local",
    ),
)
@click.version_option(version="1.0.0")
@click.option("--url", help=f"Custom API URL (default: {DEFAULT_API_BASE_URL})")
@click.pass_context
def cli(ctx, url):
    """Ridges CLI - Manage your Ridges miners and validators."""
    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


cli.add_command(upload)
cli.add_command(miner)

__all__ = ["cli"]
