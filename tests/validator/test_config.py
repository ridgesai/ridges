from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest


def _load_config(monkeypatch, *, mode: str, ridges_max_cost_usd: str):
    monkeypatch.setenv("MODE", mode)
    monkeypatch.setenv("RIDGES_MAX_COST_USD", ridges_max_cost_usd)

    if mode == "validator":
        monkeypatch.setenv("VALIDATOR_WALLET_NAME", "test-wallet")
        monkeypatch.setenv("VALIDATOR_HOTKEY_NAME", "test-hotkey")

        class FakeWallet:
            def __init__(self, *, name: str, hotkey: str):
                self.name = name
                self.hotkey_name = hotkey
                self.hotkey = SimpleNamespace(ss58_address="test-validator-hotkey")

        monkeypatch.setattr("bittensor_wallet.wallet.Wallet", FakeWallet)

    sys.modules.pop("validator.config", None)
    config = importlib.import_module("validator.config")
    return importlib.reload(config)


def test_validator_mode_hardcodes_ridges_max_cost_usd(monkeypatch) -> None:
    config = _load_config(monkeypatch, mode="validator", ridges_max_cost_usd="0.01")

    assert config.RIDGES_MAX_COST_USD == 0.29


def test_screener_mode_hardcodes_ridges_max_cost_usd(monkeypatch) -> None:
    config = _load_config(monkeypatch, mode="screener", ridges_max_cost_usd="9")

    assert config.RIDGES_MAX_COST_USD == 0.29


def test_request_evaluation_interval_is_hardcoded(monkeypatch) -> None:
    monkeypatch.setenv("REQUEST_EVALUATION_INTERVAL_SECONDS", "10")
    config = _load_config(monkeypatch, mode="screener", ridges_max_cost_usd="9")

    assert config.REQUEST_EVALUATION_INTERVAL_SECONDS == 60


def test_validator_mode_ignores_max_concurrent_evaluation_runs_env(monkeypatch) -> None:
    """Validators take concurrency from the competition, not the environment."""
    monkeypatch.setenv("MAX_CONCURRENT_EVALUATION_RUNS", "99")
    config = _load_config(monkeypatch, mode="validator", ridges_max_cost_usd="0.01")

    assert config.MAX_CONCURRENT_EVALUATION_RUNS == config.DEFAULT_MAX_CONCURRENT_EVALUATION_RUNS


def test_screener_mode_still_reads_max_concurrent_evaluation_runs_env(monkeypatch) -> None:
    monkeypatch.setenv("MAX_CONCURRENT_EVALUATION_RUNS", "30")
    config = _load_config(monkeypatch, mode="screener", ridges_max_cost_usd="0.01")

    assert config.MAX_CONCURRENT_EVALUATION_RUNS == 30


def test_screener_mode_falls_back_to_default_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("MAX_CONCURRENT_EVALUATION_RUNS", raising=False)
    config = _load_config(monkeypatch, mode="screener", ridges_max_cost_usd="0.01")

    assert config.MAX_CONCURRENT_EVALUATION_RUNS == config.SCREENER_DEFAULT_MAX_CONCURRENT_EVALUATION_RUNS


def test_platform_validator_mode_defaults_name_to_short_hostname(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_VALIDATOR_PASSWORD", "platform-password")
    monkeypatch.delenv("PLATFORM_VALIDATOR_NAME", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "platform-validator-3.example.internal")
    config = _load_config(monkeypatch, mode="platform", ridges_max_cost_usd="0.01")

    assert config.PLATFORM_VALIDATOR_NAME == "platform-validator-3"


def test_platform_validator_mode_rejects_names_outside_the_platform_validator_format(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_VALIDATOR_PASSWORD", "platform-password")
    monkeypatch.setenv("PLATFORM_VALIDATOR_NAME", "ubuntu-8gb-fsn1-1")

    with pytest.raises(Exception, match="platform-validator-NUM"):
        _load_config(monkeypatch, mode="platform", ridges_max_cost_usd="0.01")


def test_platform_validator_mode_takes_concurrency_from_the_competition(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_VALIDATOR_PASSWORD", "platform-password")
    monkeypatch.setenv("PLATFORM_VALIDATOR_NAME", "platform-validator-1")
    monkeypatch.setenv("MAX_CONCURRENT_EVALUATION_RUNS", "99")
    config = _load_config(monkeypatch, mode="platform", ridges_max_cost_usd="0.01")

    assert config.MAX_CONCURRENT_EVALUATION_RUNS == config.DEFAULT_MAX_CONCURRENT_EVALUATION_RUNS
