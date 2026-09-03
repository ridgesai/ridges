from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bittensor.core.chain_data.metagraph_info import SelectiveMetagraphIndex

from validator import set_weights as set_weights_module


@pytest.fixture
def mock_subtensor(monkeypatch):
    client = AsyncMock()
    client.set_weights.return_value = (True, "ok")
    monkeypatch.setattr(set_weights_module, "subtensor", client)
    monkeypatch.setattr(set_weights_module.config, "VALIDATOR_WALLET", object(), raising=False)
    return client


def _metagraph(*hotkeys: str) -> SimpleNamespace:
    return SimpleNamespace(hotkeys=list(hotkeys))


@pytest.mark.anyio
async def test_submits_original_ordered_weights_from_one_fresh_metagraph(mock_subtensor) -> None:
    mock_subtensor.get_metagraph_info.return_value = _metagraph("hk-2", "unused", "hk-1", "hk-3")
    mapping = {"hk-1": 0.6, "hk-2": 0.3, "hk-3": 0.1}

    await set_weights_module.set_weights_from_mapping(mapping)

    mock_subtensor.get_metagraph_info.assert_awaited_once_with(
        netuid=set_weights_module.config.NETUID,
        selected_indices=[SelectiveMetagraphIndex.Hotkeys],
    )
    kwargs = mock_subtensor.set_weights.await_args.kwargs
    assert kwargs["uids"] == [2, 0, 3]
    assert kwargs["weights"] == [0.6, 0.3, 0.1]


@pytest.mark.anyio
async def test_any_missing_hotkey_skips_the_whole_tick_without_owner_fallback(mock_subtensor) -> None:
    mock_subtensor.get_metagraph_info.return_value = _metagraph("hk-1", "hk-3")

    await set_weights_module.set_weights_from_mapping({"hk-1": 0.6, "missing": 0.3, "hk-3": 0.1})

    mock_subtensor.set_weights.assert_not_awaited()
    mock_subtensor.get_subnet_owner_hotkey.assert_not_awaited()


@pytest.mark.anyio
async def test_unresolvable_metagraph_preserves_previous_weights(mock_subtensor) -> None:
    mock_subtensor.get_metagraph_info.return_value = None

    await set_weights_module.set_weights_from_mapping({"hotkey": 1.0})

    mock_subtensor.set_weights.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mapping",
    [
        {},
        {"hk": 0},
        {"hk": -1},
        {"hk": float("nan")},
        {"hk": True},
        {"hk": 0.9},
        {"hk-1": 0.6, "hk-2": 0.5},
    ],
)
async def test_malformed_or_nonunit_weights_are_rejected_before_chain_calls(mock_subtensor, mapping) -> None:
    with pytest.raises(ValueError):
        await set_weights_module.set_weights_from_mapping(mapping)

    mock_subtensor.get_metagraph_info.assert_not_awaited()
    mock_subtensor.set_weights.assert_not_awaited()
