"""Single-agent miner CLI config: schema, defaults, and TOML persistence."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_WORKSPACE = Path.home() / ".ridges"
_RECENT_LIMIT = 5


class MinerConfigError(RuntimeError):
    """Raised when the miner CLI config cannot be parsed."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path


def default_workspace() -> Path:
    return DEFAULT_WORKSPACE


def default_config_path() -> Path:
    return Path.home() / ".config" / "ridges" / "miner.toml"


@dataclass(slots=True)
class MinerConfig:
    """Resolved miner configuration used by the CLI."""

    workspace: Path = field(default_factory=default_workspace)
    agent_path: Path | None = None
    provider: str | None = None
    recent_datasets: tuple[str, ...] = ()
    recent_problems: tuple[str, ...] = ()

    @property
    def results_dir(self) -> Path:
        return self.workspace / "runs"

    @property
    def cache_dir(self) -> Path:
        return self.workspace / "cache"

    def missing_fields(self) -> tuple[str, ...]:
        missing: list[str] = []
        if self.agent_path is None:
            missing.append("agent_path")

        return tuple(missing)

    def is_complete(self) -> bool:
        return len(self.missing_fields()) == 0


def _expect_table(data: Any, *, path: Path, label: str) -> dict[str, Any]:
    if data is None:
        return {}

    if isinstance(data, dict):
        return data

    raise MinerConfigError(path, f"{label} must be a TOML table")


def _optional_path(value: Any, *, path: Path, label: str) -> Path | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise MinerConfigError(path, f"{label} must be a string path")

    return Path(value).expanduser()


def _optional_string(value: Any, *, path: Path, label: str) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise MinerConfigError(path, f"{label} must be a string")

    return value


def _string_tuple(value: Any, *, path: Path, label: str) -> tuple[str, ...]:
    if value is None:
        return ()

    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise MinerConfigError(path, f"{label} must be a list of strings")

    return tuple(value)


def save_config(config: MinerConfig, path: Path | None = None) -> Path:
    """Write the config to TOML. Creates parent dirs as needed."""
    import tomli_w

    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "miner": {
            "workspace": str(config.workspace),
            "recent_datasets": list(config.recent_datasets),
            "recent_problems": list(config.recent_problems),
        }
    }
    if config.agent_path is not None:
        payload["miner"]["agent_path"] = str(config.agent_path)
    if config.provider:
        payload["miner"]["provider"] = config.provider

    with target.open("wb") as handle:
        tomli_w.dump(payload, handle)
    return target


def load_config(path: Path | None = None, *, allow_legacy_inference_url: bool = False) -> MinerConfig:
    """Read the config file. Missing file returns defaults."""
    target = path or default_config_path()
    if not target.exists():
        return MinerConfig()

    try:
        with target.open("rb") as handle:
            data = tomllib.load(handle)

    except tomllib.TOMLDecodeError as exception:
        raise MinerConfigError(target, f"Invalid TOML: {exception}") from exception
    except OSError as exception:
        raise MinerConfigError(target, f"Failed to read config: {exception}") from exception

    miner_section = _expect_table(data.get("miner"), path=target, label="[miner]")
    if "inference_url" in miner_section and not allow_legacy_inference_url:
        raise MinerConfigError(
            target,
            "Deprecated [miner].inference_url found. Please re-run `ridges miner setup` to migrate to provider-based local inference.",
        )
    workspace_raw = miner_section.get("workspace")
    if workspace_raw is not None and not isinstance(workspace_raw, str):
        raise MinerConfigError(target, "[miner].workspace must be a string path")

    return MinerConfig(
        workspace=Path(workspace_raw).expanduser() if workspace_raw else DEFAULT_WORKSPACE,
        agent_path=_optional_path(miner_section.get("agent_path"), path=target, label="[miner].agent_path"),
        provider=_optional_string(miner_section.get("provider"), path=target, label="[miner].provider"),
        recent_datasets=_string_tuple(
            miner_section.get("recent_datasets"),
            path=target,
            label="[miner].recent_datasets",
        ),
        recent_problems=_string_tuple(
            miner_section.get("recent_problems"),
            path=target,
            label="[miner].recent_problems",
        ),
    )


def apply_overrides(base: MinerConfig, **overrides: Any) -> MinerConfig:
    """Merge non-None keyword overrides onto a base config. CLI flags win."""
    filtered = {key: value for key, value in overrides.items() if value is not None}
    if not filtered:
        return base
    return replace(base, **filtered)


def record_recent(config: MinerConfig, *, dataset: str | None, problem: str | None) -> MinerConfig:
    """Move dataset/problem to front of their recent lists; dedupe; bound size."""

    def lifo(seq: tuple[str, ...], value: str | None) -> tuple[str, ...]:
        if not value:
            return seq

        deduped = tuple(item for item in seq if item != value)
        return (value, *deduped)[:_RECENT_LIMIT]

    return replace(
        config,
        recent_datasets=lifo(config.recent_datasets, dataset),
        recent_problems=lifo(config.recent_problems, problem),
    )
