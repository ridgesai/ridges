from unittest.mock import AsyncMock

import pytest

from validator import set_weights as set_weights_module


@pytest.fixture
def mock_subtensor(monkeypatch):
    client = AsyncMock()
    client.set_weights.return_value = (True, "ok")
    monkeypatch.setattr(set_weights_module, "subtensor", client)
    monkeypatch.setattr(set_weights_module.config, "VALIDATOR_WALLET", object(), raising=False)
    return client


@pytest.mark.anyio
async def test_submits_multiple_normalized_weights(mock_subtensor) -> None:
    mock_subtensor.get_uid_for_hotkey_on_subnet.side_effect = [10, 20, 30]

    await set_weights_module.set_weights_from_mapping({"hk-1": 0.6, "hk-2": 0.3, "hk-3": 0.1})

    kwargs = mock_subtensor.set_weights.await_args.kwargs
    assert kwargs["uids"] == [10, 20, 30]
    assert kwargs["weights"] == pytest.approx([0.6, 0.3, 0.1])


@pytest.mark.anyio
async def test_missing_uid_is_dropped_and_remaining_weights_are_renormalized(mock_subtensor) -> None:
    mock_subtensor.get_uid_for_hotkey_on_subnet.side_effect = [10, None, 30]

    await set_weights_module.set_weights_from_mapping({"hk-1": 0.6, "missing": 0.3, "hk-3": 0.1})

    kwargs = mock_subtensor.set_weights.await_args.kwargs
    assert kwargs["uids"] == [10, 30]
    assert kwargs["weights"] == pytest.approx([6 / 7, 1 / 7])


@pytest.mark.anyio
async def test_all_missing_uids_fall_back_to_subnet_owner(mock_subtensor) -> None:
    mock_subtensor.get_uid_for_hotkey_on_subnet.side_effect = [None, 99]
    mock_subtensor.get_subnet_owner_hotkey.return_value = "owner-hk"

    await set_weights_module.set_weights_from_mapping({"missing": 1.0})

    mock_subtensor.get_subnet_owner_hotkey.assert_awaited_once_with(netuid=set_weights_module.config.NETUID)
    kwargs = mock_subtensor.set_weights.await_args.kwargs
    assert kwargs["uids"] == [99]
    assert kwargs["weights"] == [1.0]


@pytest.mark.anyio
async def test_missing_subnet_owner_preserves_previous_weights(mock_subtensor) -> None:
    mock_subtensor.get_uid_for_hotkey_on_subnet.return_value = None
    mock_subtensor.get_subnet_owner_hotkey.return_value = None

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

    mock_subtensor.get_uid_for_hotkey_on_subnet.assert_not_awaited()
    mock_subtensor.set_weights.assert_not_awaited()
