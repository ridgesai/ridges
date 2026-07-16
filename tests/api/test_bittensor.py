from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bittensor.core.chain_data.metagraph_info import SelectiveMetagraphIndex

from utils.bittensor import HotkeySubnetInfo, SubtensorClient


@pytest.mark.anyio
async def test_get_subnet_hotkey_info_uses_selective_metagraph() -> None:
    metagraph = SimpleNamespace(
        hotkeys=["registered", "zero-emission"],
        emission=[SimpleNamespace(tao=147.600823658), SimpleNamespace(tao=0.0)],
    )
    subtensor = SimpleNamespace(get_metagraph_info=AsyncMock(return_value=metagraph))
    client = SubtensorClient()
    client._subtensor = subtensor

    result = await client.get_subnet_hotkey_info(netuid=62)

    assert result == {
        "registered": HotkeySubnetInfo(uid=0, emission=147.600823658),
        "zero-emission": HotkeySubnetInfo(uid=1, emission=0.0),
    }
    subtensor.get_metagraph_info.assert_awaited_once_with(
        netuid=62,
        selected_indices=[
            SelectiveMetagraphIndex.Hotkeys,
            SelectiveMetagraphIndex.Emission,
        ],
    )


@pytest.mark.anyio
async def test_get_subnet_hotkey_info_handles_missing_emission_entry() -> None:
    subtensor = SimpleNamespace(
        get_metagraph_info=AsyncMock(
            return_value=SimpleNamespace(
                hotkeys=["with-emission", "without-emission"],
                emission=[SimpleNamespace(tao=1.5)],
            )
        )
    )
    client = SubtensorClient()
    client._subtensor = subtensor

    assert await client.get_subnet_hotkey_info(netuid=62) == {
        "with-emission": HotkeySubnetInfo(uid=0, emission=1.5),
        "without-emission": HotkeySubnetInfo(uid=1, emission=None),
    }


@pytest.mark.anyio
async def test_get_subnet_hotkey_info_rejects_missing_metagraph() -> None:
    subtensor = SimpleNamespace(get_metagraph_info=AsyncMock(return_value=None))
    client = SubtensorClient()
    client._subtensor = subtensor

    with pytest.raises(RuntimeError, match="Could not retrieve hotkeys"):
        await client.get_subnet_hotkey_info(netuid=62)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("position_rao", "total_rao", "locked_rao", "available_rao", "expected_burnable"),
    [
        (5_000, 20_000, 0, 20_000, 5_000),
        (20_000, 30_000, 25_000, 5_000, 5_000),
        (20_000, 20_000, 25_000, 0, 0),
    ],
)
async def test_alpha_stake_availability_applies_position_and_subnet_lock_limits(
    position_rao: int,
    total_rao: int,
    locked_rao: int,
    available_rao: int,
    expected_burnable: int,
) -> None:
    block_hash = "0xchain-head"
    netuid = 62
    coldkey = "coldkey"
    hotkey = "hotkey"

    substrate = SimpleNamespace(get_chain_head=AsyncMock(return_value=block_hash))
    fake_subtensor = SimpleNamespace(
        substrate=substrate,
        get_stake=AsyncMock(return_value=SimpleNamespace(rao=position_rao)),
        get_stake_availability_for_coldkeys=AsyncMock(
            return_value={
                coldkey: {
                    netuid: {
                        "total": total_rao,
                        "locked": locked_rao,
                        "available": available_rao,
                    }
                }
            }
        ),
    )
    client = SubtensorClient()
    client._subtensor = fake_subtensor

    result = await client.get_alpha_stake_availability(coldkey=coldkey, hotkey=hotkey, netuid=netuid)

    assert result.block_hash == block_hash
    assert result.position_rao == position_rao
    assert result.total_rao == total_rao
    assert result.locked_rao == locked_rao
    assert result.available_rao == available_rao
    assert result.burnable_rao == expected_burnable
    fake_subtensor.get_stake.assert_awaited_once_with(
        coldkey_ss58=coldkey,
        hotkey_ss58=hotkey,
        netuid=netuid,
        block_hash=block_hash,
    )
    fake_subtensor.get_stake_availability_for_coldkeys.assert_awaited_once_with(
        [coldkey],
        netuids=[netuid],
        block_hash=block_hash,
    )


@pytest.mark.anyio
async def test_alpha_stake_availability_treats_omitted_zero_subnet_as_zero() -> None:
    block_hash = "0xchain-head"
    coldkey = "coldkey"
    netuid = 62
    fake_subtensor = SimpleNamespace(
        substrate=SimpleNamespace(get_chain_head=AsyncMock(return_value=block_hash)),
        get_stake=AsyncMock(return_value=SimpleNamespace(rao=0)),
        get_stake_availability_for_coldkeys=AsyncMock(return_value={coldkey: {}}),
    )
    client = SubtensorClient()
    client._subtensor = fake_subtensor

    result = await client.get_alpha_stake_availability(coldkey=coldkey, hotkey="hotkey", netuid=netuid)

    assert result.position_rao == 0
    assert result.total_rao == 0
    assert result.locked_rao == 0
    assert result.available_rao == 0
    assert result.burnable_rao == 0
