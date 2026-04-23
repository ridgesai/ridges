"""Interactive prompt and rendering helpers for `ridges miner`."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Sequence

from rich.console import Console

from miners.cli.config import MinerConfig
from miners.cli.provider_env import ProviderStatus
from miners.cli.registry import DatasetInfo, ProblemInfo

console = Console()


def _short_path(path: Path | None) -> str:
    if path is None:
        return "[not set]"

    resolved = path.expanduser()
    text = str(resolved)
    home = str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + "/"):
        return "~/" + text[len(home) + 1 :]
    return text


def _provider_text(provider: str | None) -> str:
    if provider is None:
        return "[not set yet]"

    labels = {
        "openrouter": "OpenRouter",
        "targon": "Targon",
        "chutes": "Chutes",
        "custom": "custom sandbox proxy",
    }
    return labels.get(provider, provider)


def _picker_style():
    from InquirerPy import get_style

    return get_style(
        {
            "questionmark": "#38bdf8",
            "question": "bold",
            "instruction": "#94a3b8",
            "pointer": "#38bdf8 bold",
            "fuzzy_prompt": "#38bdf8 bold",
            "fuzzy_info": "#94a3b8",
            "fuzzy_match": "#38bdf8 bold",
        },
        style_override=False,
    )


def _dataset_choice_label(
    info: DatasetInfo,
    *,
    description: str | None = None,
    recommended: bool = False,
    recent: bool = False,
) -> str:
    suffixes: list[str] = []
    if recommended:
        suffixes.append("recommended")
    if recent:
        suffixes.append("recent")
    if description:
        suffixes.append(description)
    if not suffixes:
        return info.label
    return f"{info.label}  ·  " + "  ·  ".join(suffixes)


def _problem_choice_label(info: ProblemInfo, *, recent: bool = False) -> str:
    if not recent:
        return info.name
    return f"{info.name}  ·  recent"


def render_brand(subtitle: str) -> None:
    from rich.text import Text

    console.print()
    console.print(_brand_text(wide=True))
    console.print(Text(subtitle, style="dim"))
    console.print()


def render_step(label: str, *, step: str | None = None) -> None:
    from rich.text import Text

    text = Text()
    if step:
        text.append(f"[{step}] ", style="bold cyan")
    text.append(label, style="bold white")
    console.print(text)


def _brand_text(*, wide: bool = False):
    from rich.text import Text

    title = Text()
    title.append("╱╲╱╲╱╲╱╲╱╲╱╲ " if wide else "╱╲╱╲╱╲ ", style="bold cyan")
    title.append("Ridges", style="bold cyan")
    return title


def _render_card(subtitle: str, rows: Sequence[tuple[str, str]]) -> None:
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold white", no_wrap=True)
    table.add_column(style="white")
    for label, value in rows:
        table.add_row(label, value)

    body = Group(_brand_text(), Text(subtitle, style="dim"), table)
    console.print(Panel.fit(body, border_style="cyan"))


def prompt_workspace(default: Path) -> Path:
    from InquirerPy import inquirer

    raw = inquirer.filepath(
        message="Workspace directory (downloads + run results):",
        default=str(default),
        only_directories=True,
    ).execute()
    return Path(raw).expanduser()


def prompt_agent_path(default: Path | None) -> Path:
    from InquirerPy import inquirer

    raw = inquirer.filepath(
        message="Path to your agent.py:",
        default=str(default) if default else "",
        only_directories=False,
    ).execute()
    return Path(raw).expanduser()


def prompt_agent_candidate(candidates: list[Path], *, default: Path | None) -> str:
    """Prompt for a discovered candidate, manual path entry, or skip."""
    from InquirerPy import inquirer

    choices = [{"name": f"Use {candidate}", "value": str(candidate)} for candidate in candidates]
    choices.extend(
        [
            {"name": "Choose another file", "value": "__other__"},
            {"name": "Skip for now", "value": "__skip__"},
        ]
    )
    default_value = str(default) if default is not None and default in candidates else None
    return inquirer.select(message="Select an agent file:", choices=choices, default=default_value).execute()


def prompt_confirm(message: str, default: bool = True) -> bool:
    from InquirerPy import inquirer

    return inquirer.confirm(message=message, default=default).execute()


def render_setup_preview(config: MinerConfig) -> None:
    _render_card(
        "Ready to save",
        [
            ("Workspace", _short_path(config.workspace)),
            ("Agent", _short_path(config.agent_path)),
            ("Provider", _provider_text(config.provider)),
            ("Env", _short_path(config.workspace / ".env.miner")),
            ("Results", _short_path(config.results_dir)),
            ("Cache", _short_path(config.cache_dir)),
        ],
    )


class MainChoice(str, Enum):
    CONTINUE = "continue"
    SWITCH_AGENT = "switch_agent"
    SWITCH_PROVIDER = "switch_provider"
    EDIT_WORKSPACE = "edit_workspace"
    USE_LOCAL_TASK = "use_local_task"
    CANCEL = "cancel"


def prompt_main_choice() -> MainChoice:
    from InquirerPy import inquirer

    selected = inquirer.select(
        message="What next?",
        choices=[
            {"name": "Continue with current defaults", "value": MainChoice.CONTINUE},
            {"name": "Switch agent", "value": MainChoice.SWITCH_AGENT},
            {"name": "Switch provider", "value": MainChoice.SWITCH_PROVIDER},
            {"name": "Edit workspace", "value": MainChoice.EDIT_WORKSPACE},
            {"name": "Use local task path instead", "value": MainChoice.USE_LOCAL_TASK},
            {"name": "Cancel", "value": MainChoice.CANCEL},
        ],
        default=MainChoice.CONTINUE,
    ).execute()
    return selected


def render_summary_card(config: MinerConfig) -> None:
    _render_card(
        "Run an agent locally",
        [
            ("Agent", _short_path(config.agent_path)),
            ("Provider", _provider_text(config.provider)),
            ("Workspace", _short_path(config.workspace)),
            ("Results", _short_path(config.results_dir)),
            ("Cache", _short_path(config.cache_dir)),
            ("Env", _short_path(config.workspace / ".env.miner")),
        ],
    )


def render_config_card(config: MinerConfig, *, config_path: Path) -> None:
    _render_card(
        "Inspect saved miner config",
        [
            ("Config", _short_path(config_path)),
            ("Workspace", _short_path(config.workspace)),
            ("Agent", _short_path(config.agent_path)),
            ("Provider", _provider_text(config.provider)),
            ("Env", _short_path(config.workspace / ".env.miner")),
            ("Results", _short_path(config.results_dir)),
            ("Cache", _short_path(config.cache_dir)),
        ],
    )


def prompt_select_provider(configured: Sequence[ProviderStatus], *, default: str | None) -> str:
    from InquirerPy import inquirer

    choices = [
        {
            "name": f"{status.label} ({status.detail})",
            "value": status.provider,
        }
        for status in configured
    ]
    default_value = default if default in {status.provider for status in configured} else None
    return inquirer.select(message="Provider", choices=choices, default=default_value).execute()


def prompt_select_dataset(
    datasets: list[DatasetInfo],
    recent: tuple[str, ...],
    *,
    recommended: Sequence[tuple[str, str]],
) -> str:
    """Dataset picker. Recommended first, then recent, then the rest."""
    from InquirerPy import inquirer

    by_id = {dataset.id: dataset for dataset in datasets}
    choices: list[dict[str, str]] = []
    seen: set[str] = set()

    for dataset_id, description in recommended:
        info = by_id.get(dataset_id)
        if info is None:
            continue
        choices.append(
            {
                "name": _dataset_choice_label(
                    info,
                    description=description,
                    recommended=True,
                ),
                "value": info.id,
            }
        )
        seen.add(info.id)

    for dataset_id in recent:
        info = by_id.get(dataset_id)
        if info is None or info.id in seen:
            continue
        choices.append(
            {
                "name": _dataset_choice_label(info, recent=True),
                "value": info.id,
            }
        )
        seen.add(info.id)

    for info in datasets:
        if info.id in seen:
            continue
        choices.append({"name": info.label, "value": info.id})

    return inquirer.fuzzy(
        message="Dataset",
        instruction="type to filter",
        choices=choices,
        max_height="50%",
        info=False,
        prompt="›",
        style=_picker_style(),
    ).execute()


def prompt_select_problem(problems: list[ProblemInfo], recent: tuple[str, ...]) -> str:
    """Problem picker with fuzzy search."""
    from InquirerPy import inquirer

    by_id = {problem.id: problem for problem in problems}
    choices: list[dict[str, str]] = []
    seen: set[str] = set()

    for problem_id in recent:
        info = by_id.get(problem_id)
        if info is None:
            continue
        choices.append({"name": _problem_choice_label(info, recent=True), "value": problem_id})
        seen.add(problem_id)

    for info in problems:
        if info.id in seen:
            continue
        choices.append({"name": info.name, "value": info.id})

    return inquirer.fuzzy(
        message="Problem",
        instruction="type to filter",
        choices=choices,
        max_height="70%",
        info=False,
        prompt="›",
        style=_picker_style(),
    ).execute()


def prompt_local_task_path() -> Path:
    from InquirerPy import inquirer

    raw = inquirer.filepath(
        message="Local task directory or archive:",
        only_directories=False,
    ).execute()
    return Path(raw).expanduser()
