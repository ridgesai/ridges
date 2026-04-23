"""`ridges miner run-local` command and orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from miners import run_local_task
from miners.cli.click_ext import click, format_help
from miners.cli.config import (
    MinerConfig,
    apply_overrides,
    default_config_path,
    record_recent,
    save_config,
)
from miners.cli.prompts import (
    MainChoice,
    prompt_local_task_path,
    prompt_main_choice,
    prompt_select_dataset,
    prompt_select_problem,
    prompt_select_provider,
    prompt_workspace,
    render_step,
    render_summary_card,
)
from miners.cli.provider_env import (
    configured_provider_statuses,
    missing_provider_message,
    provider_statuses,
    resolve_inference_config,
)
from miners.cli.registry import HarborRegistryAdapter
from miners.local_harbor import LocalRunInferenceConfig

from .setup import _run_setup_flow
from .shared import (
    RECOMMENDED_DATASETS,
    _load_config_or_exit,
    console,
    print_provider_setup_hint,
    prompt_validated_agent_path,
)


def _switch_agent(cfg: MinerConfig, config_target: Path) -> MinerConfig:
    new_path = prompt_validated_agent_path(workspace=cfg.workspace, default=cfg.agent_path)
    if new_path is None:
        return cfg
    updated = replace(cfg, agent_path=new_path)
    save_config(updated, config_target)
    return updated


def _switch_provider(cfg: MinerConfig, config_target: Path) -> MinerConfig:
    configured = configured_provider_statuses(cfg.workspace)
    if not configured:
        print_provider_setup_hint(cfg.workspace)
        return cfg
    updated = replace(cfg, provider=prompt_select_provider(configured, default=cfg.provider))
    save_config(updated, config_target)
    return updated


def _edit_workspace(cfg: MinerConfig, config_target: Path) -> MinerConfig:
    new_ws = prompt_workspace(cfg.workspace)
    updated = replace(cfg, workspace=new_ws)
    save_config(updated, config_target)
    return updated


def _select_and_download_problem(
    *,
    adapter: HarborRegistryAdapter,
    cache_dir: Path,
    recent_datasets: tuple[str, ...],
    recent_problems: tuple[str, ...],
) -> tuple[str, str, Path]:
    datasets = adapter.list_datasets()
    dataset_id = prompt_select_dataset(datasets, recent_datasets, recommended=RECOMMENDED_DATASETS)
    problems = adapter.list_problems(dataset_id)
    problem_id = prompt_select_problem(problems, recent_problems)
    task_path = adapter.download_problem(dataset_id, problem_id, dest=cache_dir)
    return dataset_id, problem_id, task_path


def _count_test_results(test_results) -> tuple[int, int, int]:
    passed = sum(1 for test in test_results if test.status == "pass")
    failed = sum(1 for test in test_results if test.status == "fail")
    skipped = sum(1 for test in test_results if test.status == "skip")
    return passed, failed, skipped


async def _run_local_task_and_report(
    task_path,
    *,
    agent_path,
    inference: LocalRunInferenceConfig,
    results_dir=None,
    debug: bool = False,
    emit_mode_warning: bool = True,
) -> int:
    """Run one local Harbor task and print the final miner-facing summary."""
    if emit_mode_warning:
        print("Warning: local mode does not enforce evaluation sandbox restrictions.")

    try:
        summary = await run_local_task(
            task_path,
            agent_path=agent_path,
            inference=inference,
            results_dir=results_dir,
            debug=debug,
        )
    except Exception as exception:
        print(f"ERROR: {type(exception).__name__}: {exception}", file=click.get_text_stream("stderr"))
        return 2

    from execution.artifacts import result_from_summary
    from execution.errors import EvaluationRunException

    try:
        result = result_from_summary(summary)
    except EvaluationRunException as exception:
        print(f"FAILED: {exception.error_code.name}")
        print(f"message: {exception.error_message}")
        print(f"job_dir: {summary.job_dir}")
        print(f"trial_dir: {summary.trial_dir}")
        return 1

    passed, failed, skipped = _count_test_results(result.test_results)
    print("SUCCEEDED")
    print(f"reward: {result.verifier_reward}")
    print(f"tests: {len(result.test_results)} total ({passed} passed, {failed} failed, {skipped} skipped)")
    print(f"job_dir: {summary.job_dir}")
    print(f"trial_dir: {summary.trial_dir}")
    print(f"task_dir: {summary.task_dir}")
    return 0


def _resolve_selected_provider(
    cfg: MinerConfig,
    *,
    config_target: Path,
    non_interactive: bool,
) -> tuple[MinerConfig, LocalRunInferenceConfig]:
    statuses = provider_statuses(cfg.workspace)
    configured = [status for status in statuses.values() if status.configured]

    if cfg.provider:
        status = statuses.get(cfg.provider)
        if status is not None and status.configured:
            return cfg, resolve_inference_config(cfg.provider, cfg.workspace)

        if non_interactive or not configured:
            raise click.ClickException(missing_provider_message(cfg.provider, cfg.workspace))

        console.print(f"[yellow]Selected provider {cfg.provider} is not configured.[/yellow]")
        updated = replace(cfg, provider=prompt_select_provider(configured, default=configured[0].provider))
        save_config(updated, config_target)
        return updated, resolve_inference_config(updated.provider or "", updated.workspace)

    if not configured:
        raise click.ClickException(missing_provider_message(None, cfg.workspace))

    if len(configured) == 1:
        updated = replace(cfg, provider=configured[0].provider)
        save_config(updated, config_target)
        return updated, resolve_inference_config(updated.provider or "", updated.workspace)

    if non_interactive:
        names = ", ".join(status.provider for status in configured)
        raise click.ClickException(
            "Multiple local providers are configured. Set a default with `ridges miner setup` "
            f"or pass --provider. Configured providers: {names}"
        )

    updated = replace(cfg, provider=prompt_select_provider(configured, default=None))
    save_config(updated, config_target)
    return updated, resolve_inference_config(updated.provider or "", updated.workspace)


def run_local_flow(
    *,
    config_path: Path | None,
    agent_path: Path | None,
    provider: str | None,
    workspace: Path | None,
    results_dir: Path | None,
    task_path: Path | None,
    dataset: str | None,
    problem: str | None,
    debug: bool,
    non_interactive: bool,
) -> int:
    """Run the full `ridges miner run-local` flow and return the exit code."""
    config_target = config_path or default_config_path()
    cfg = _load_config_or_exit(config_target)
    cfg = apply_overrides(
        cfg,
        workspace=workspace,
        provider=provider,
        agent_path=agent_path,
    )

    resolved_task_path = task_path
    inference: LocalRunInferenceConfig | None = None

    if non_interactive:
        missing: list[str] = []
        if not cfg.agent_path:
            missing.append("agent_path (--agent-path or config [miner].agent_path)")
        if resolved_task_path is None and not (dataset and problem):
            missing.append("--task-path OR (--dataset and --problem)")
        if missing:
            raise click.ClickException("Missing required values in non-interactive mode: " + "; ".join(missing))
    else:
        if not cfg.is_complete():
            cfg = _run_setup_flow(existing=cfg, config_path=config_target)

        if resolved_task_path is None and not (dataset and problem):
            while resolved_task_path is None and not (dataset and problem):
                render_summary_card(cfg)
                choice = prompt_main_choice()
                if choice is MainChoice.CANCEL:
                    return 0
                if choice is MainChoice.CONTINUE:
                    break
                if choice is MainChoice.SWITCH_AGENT:
                    cfg = _switch_agent(cfg, config_target)
                    continue
                if choice is MainChoice.SWITCH_PROVIDER:
                    cfg = _switch_provider(cfg, config_target)
                    continue
                if choice is MainChoice.EDIT_WORKSPACE:
                    cfg = _edit_workspace(cfg, config_target)
                    continue
                if choice is MainChoice.USE_LOCAL_TASK:
                    resolved_task_path = prompt_local_task_path()
                    break

            if resolved_task_path is None and not (dataset and problem):
                cfg, inference = _resolve_selected_provider(
                    cfg,
                    config_target=config_target,
                    non_interactive=non_interactive,
                )
                render_step("Choose task", step="2/3")
                adapter = HarborRegistryAdapter.build()
                dataset_id, problem_id, resolved_task_path = _select_and_download_problem(
                    adapter=adapter,
                    cache_dir=cfg.cache_dir,
                    recent_datasets=cfg.recent_datasets,
                    recent_problems=cfg.recent_problems,
                )
                cfg = record_recent(cfg, dataset=dataset_id, problem=problem_id)
                save_config(cfg, config_target)

    if inference is None:
        cfg, inference = _resolve_selected_provider(cfg, config_target=config_target, non_interactive=non_interactive)

    if resolved_task_path is None and dataset and problem:
        render_step("Fetch task", step="2/3")
        adapter = HarborRegistryAdapter.build()
        cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        resolved_task_path = adapter.download_problem(dataset, problem, dest=cfg.cache_dir)
        cfg = record_recent(cfg, dataset=dataset, problem=problem)
        save_config(cfg, config_target)

    if resolved_task_path is None:
        raise click.ClickException("No task path resolved; nothing to run.")

    effective_results_dir = results_dir or cfg.results_dir
    render_step("Run Harbor", step="3/3")
    return asyncio.run(
        _run_local_task_and_report(
            resolved_task_path,
            agent_path=cfg.agent_path,
            inference=inference,
            results_dir=effective_results_dir,
            debug=debug,
            emit_mode_warning=True,
        )
    )


@click.command(
    "run-local",
    short_help="Run one Harbor task locally.",
    help=format_help(
        "Run one Harbor task locally using the current miner config and selected local provider.",
        "ridges miner run-local",
        "ridges miner run-local --task-path ./task --provider custom",
        "ridges miner run-local --dataset swebench-verified@1.0 --problem astropy__astropy-7166",
    ),
)
@click.option("--config-path", default=None, help="Read config from this path.")
@click.option("--workspace", default=None, help="Override the configured workspace.")
@click.option("--results-dir", default=None, help="Override the derived results directory.")
@click.option("--agent-path", default=None, help="Override the configured agent.py path.")
@click.option(
    "--provider",
    default=None,
    type=click.Choice(["openrouter", "targon", "chutes", "custom"]),
    help="Override the configured local provider.",
)
@click.option("--task-path", default=None, help="Local Harbor task dir or .tar.gz/.tgz archive.")
@click.option("--dataset", default=None, help="Harbor registry dataset ref, e.g. swebench-verified@1.0")
@click.option("--problem", default=None, help="Problem id within the selected Harbor dataset.")
@click.option("--debug", is_flag=True, help="Enable Harbor debug logging.")
@click.option("--non-interactive", is_flag=True, help="Disable prompts; require CLI/config inputs.")
def run_local_command(
    config_path: str | None,
    workspace: str | None,
    results_dir: str | None,
    agent_path: str | None,
    provider: str | None,
    task_path: str | None,
    dataset: str | None,
    problem: str | None,
    debug: bool,
    non_interactive: bool,
) -> None:
    exit_code = run_local_flow(
        config_path=Path(config_path).expanduser() if config_path else None,
        agent_path=Path(agent_path).expanduser() if agent_path else None,
        provider=provider,
        workspace=Path(workspace).expanduser() if workspace else None,
        results_dir=Path(results_dir).expanduser() if results_dir else None,
        task_path=Path(task_path).expanduser() if task_path else None,
        dataset=dataset,
        problem=problem,
        debug=debug,
        non_interactive=non_interactive,
    )
    raise click.exceptions.Exit(exit_code)
