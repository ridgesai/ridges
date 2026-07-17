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
async def test_submits_multiple_normalized_weights_from_one_metagraph_read(mock_subtensor) -> None:
    mock_subtensor.get_metagraph_info.return_value = _metagraph("hk-1", "hk-2", "hk-3")

    await set_weights_module.set_weights_from_mapping({"hk-1": 0.6, "hk-2": 0.3, "hk-3": 0.1})

    mock_subtensor.get_metagraph_info.assert_awaited_once_with(
        netuid=set_weights_module.config.NETUID,
        selected_indices=[SelectiveMetagraphIndex.Hotkeys],
    )
    kwargs = mock_subtensor.set_weights.await_args.kwargs
    assert kwargs["uids"] == [0, 1, 2]
    assert kwargs["weights"] == pytest.approx([0.6, 0.3, 0.1])


@pytest.mark.anyio
async def test_missing_uid_is_dropped_and_remaining_weights_are_renormalized(mock_subtensor) -> None:
    mock_subtensor.get_metagraph_info.return_value = _metagraph("hk-1", "hk-3")

    await set_weights_module.set_weights_from_mapping({"hk-1": 0.6, "missing": 0.3, "hk-3": 0.1})

    kwargs = mock_subtensor.set_weights.await_args.kwargs
    assert kwargs["uids"] == [0, 1]
    assert kwargs["weights"] == pytest.approx([6 / 7, 1 / 7])


@pytest.mark.anyio
async def test_all_missing_uids_fall_back_to_subnet_owner_from_same_snapshot(mock_subtensor) -> None:
    mock_subtensor.get_metagraph_info.return_value = _metagraph("other-hk", "owner-hk")
    mock_subtensor.get_subnet_owner_hotkey.return_value = "owner-hk"

    await set_weights_module.set_weights_from_mapping({"missing": 1.0})

    mock_subtensor.get_subnet_owner_hotkey.assert_awaited_once_with(netuid=set_weights_module.config.NETUID)
    kwargs = mock_subtensor.set_weights.await_args.kwargs
    assert kwargs["uids"] == [1]
    assert kwargs["weights"] == [1.0]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("metagraph", "owner_hotkey"),
    [
        (None, None),
        (_metagraph("other-hk"), None),
        (_metagraph("other-hk"), "owner-hk"),
    ],
)
async def test_unresolvable_metagraph_or_owner_preserves_previous_weights(
    mock_subtensor,
    metagraph,
    owner_hotkey,
) -> None:
    mock_subtensor.get_metagraph_info.return_value = metagraph
    mock_subtensor.get_subnet_owner_hotkey.return_value = owner_hotkey

    await set_weights_module.set_weights_from_mapping({"missing": 1.0})

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
    ],
)
async def test_malformed_weights_are_rejected_before_chain_calls(mock_subtensor, mapping) -> None:
    with pytest.raises(ValueError):
        await set_weights_module.set_weights_from_mapping(mapping)

    mock_subtensor.get_metagraph_info.assert_not_awaited()
    mock_subtensor.set_weights.assert_not_awaited()
