"""`ridges miner cleanup` command."""

from __future__ import annotations

from pathlib import Path

from miners.cli.click_ext import click, format_help
from miners.local_harbor import list_task_staging_cache_dirs, prune_task_staging_cache, task_staging_cache_dir

from .shared import console, resolve_effective_results_dir


def cleanup_task_cache_flow(
    *,
    config_path: Path | None,
    workspace: Path | None,
    results_dir: Path | None,
    max_age_hours: float | None,
    dry_run: bool,
) -> int:
    """Inspect or prune the extracted archive cache used by local Harbor runs."""
    effective_results_dir = resolve_effective_results_dir(
        config_path=config_path,
        workspace=workspace,
        results_dir=results_dir,
    )
    max_age_seconds = max_age_hours * 3600 if max_age_hours is not None else None
    cache_dir = task_staging_cache_dir(effective_results_dir)

    if dry_run:
        cached_dirs = list_task_staging_cache_dirs(effective_results_dir, max_age_seconds=max_age_seconds)
        if not cached_dirs:
            console.print(f"[bold]task cache:[/bold] {cache_dir}")
            console.print("No cached extracted tasks to remove.")
            return 0

        console.print(f"[bold]task cache:[/bold] {cache_dir}")
        console.print(f"Would remove {len(cached_dirs)} cached extracted task(s):")
        for path in cached_dirs:
            console.print(f"  - {path.name}")
        return 0

    removed = prune_task_staging_cache(effective_results_dir, max_age_seconds=max_age_seconds)
    console.print(f"[bold]task cache:[/bold] {cache_dir}")
    if not removed:
        console.print("No cached extracted tasks removed.")
        return 0

    console.print(f"Removed {len(removed)} cached extracted task(s):")
    for path in removed:
        console.print(f"  - {path.name}")
    return 0


@click.command(
    "cleanup",
    short_help="Prune cached extracted task archives.",
    help=format_help(
        "Inspect or remove cached extracted task archives created by local Harbor runs.",
        "ridges miner cleanup --dry-run",
        "ridges miner cleanup --max-age-hours 24",
    ),
)
@click.option("--config-path", default=None, help="Read config from this path.")
@click.option("--workspace", default=None, help="Override the configured workspace.")
@click.option("--results-dir", default=None, help="Override the derived results directory.")
@click.option(
    "--max-age-hours",
    type=float,
    default=None,
    help="Only remove cached extracted tasks older than this many hours.",
)
@click.option("--dry-run", is_flag=True, help="Show what would be removed without deleting anything.")
def cleanup_command(
    config_path: str | None,
    workspace: str | None,
    results_dir: str | None,
    max_age_hours: float | None,
    dry_run: bool,
) -> None:
    exit_code = cleanup_task_cache_flow(
        config_path=Path(config_path) if config_path else None,
        workspace=Path(workspace).expanduser() if workspace else None,
        results_dir=Path(results_dir).expanduser() if results_dir else None,
        max_age_hours=max_age_hours,
        dry_run=dry_run,
    )
    raise click.exceptions.Exit(exit_code)
