from pathlib import Path

import pytest

from miners.cli.config import (
    DEFAULT_WORKSPACE,
    MinerConfig,
    MinerConfigError,
    apply_overrides,
    load_config,
    record_recent,
    save_config,
)


def test_miner_config_derived_dirs() -> None:
    cfg = MinerConfig(workspace=Path("/tmp/ridges"))
    assert cfg.results_dir == Path("/tmp/ridges/runs")
    assert cfg.cache_dir == Path("/tmp/ridges/cache")


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    cfg = MinerConfig(
        workspace=tmp_path / "ws",
        agent_path=tmp_path / "agent.py",
        provider="openrouter",
        recent_datasets=("polyglot@1.0",),
        recent_problems=("astropy__astropy-7166",),
    )
    config_path = tmp_path / "miner.toml"
    save_config(cfg, config_path)

    loaded = load_config(config_path)

    assert loaded == cfg
    raw = config_path.read_text()
    assert 'agent_path = "' in raw
    assert 'provider = "openrouter"' in raw


def test_load_config_missing_file_returns_defaults(tmp_path: Path) -> None:
    loaded = load_config(tmp_path / "nope.toml")
    assert loaded.workspace == DEFAULT_WORKSPACE
    assert loaded.provider is None
    assert loaded.agent_path is None


def test_load_config_raises_clean_error_for_malformed_toml(tmp_path: Path) -> None:
    path = tmp_path / "miner.toml"
    path.write_text("[miner\nworkspace = '/tmp/ws'\n")

    with pytest.raises(MinerConfigError) as exc_info:
        load_config(path)

    assert exc_info.value.path == path
    assert "Invalid TOML" in str(exc_info.value)


def test_load_config_rejects_deprecated_inference_url(tmp_path: Path) -> None:
    path = tmp_path / "miner.toml"
    path.write_text("[miner]\ninference_url = 'http://127.0.0.1:1234'\n")

    with pytest.raises(MinerConfigError) as exc_info:
        load_config(path)

    assert "inference_url" in str(exc_info.value)
    assert "re-run `ridges miner setup`" in str(exc_info.value)


def test_overrides_apply_on_top_of_loaded_config(tmp_path: Path) -> None:
    base = MinerConfig(
        workspace=tmp_path / "ws",
        agent_path=tmp_path / "file-agent.py",
        provider="openrouter",
    )
    merged = apply_overrides(
        base,
        workspace=tmp_path / "flag-ws",
        provider="targon",
    )
    assert merged.workspace == tmp_path / "flag-ws"
    assert merged.provider == "targon"
    assert merged.agent_path == tmp_path / "file-agent.py"


def test_record_recent_is_lifo_bounded(tmp_path: Path) -> None:
    base = MinerConfig(workspace=tmp_path, recent_datasets=("a", "b", "c"))
    after = record_recent(base, dataset="d", problem="x")
    assert after.recent_datasets == ("d", "a", "b", "c")
    assert after.recent_problems == ("x",)

    dup = record_recent(after, dataset="a", problem="x")
    assert dup.recent_datasets == ("a", "d", "b", "c")
    assert dup.recent_problems == ("x",)
