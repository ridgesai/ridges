from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace


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
    config = _load_config(monkeypatch, mode="screener", ridges_max_cost_usd="9999")

    assert config.RIDGES_MAX_COST_USD == 0.29
