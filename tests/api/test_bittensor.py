from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from utils.bittensor import SubtensorClient


class FakeStorageKey:
    def __init__(self, value: str) -> None:
        self.value = value

    def to_hex(self) -> str:
        return self.value


@pytest.mark.anyio
async def test_get_uids_for_hotkeys_on_subnet_uses_one_multi_query() -> None:
    storage_keys = [FakeStorageKey("0x1"), FakeStorageKey("0x2")]
    substrate = SimpleNamespace(
        create_storage_keys=AsyncMock(return_value=storage_keys),
        query_multi=AsyncMock(return_value=[(storage_keys[0], 180), (storage_keys[1], None)]),
    )
    client = SubtensorClient()
    client._subtensor = SimpleNamespace(substrate=substrate)

    result = await client.get_uids_for_hotkeys_on_subnet(["registered", "missing"], netuid=62)

    assert result == {"registered": 180, "missing": None}
    substrate.create_storage_keys.assert_awaited_once_with(
        pallet="SubtensorModule",
        storage_function="Uids",
        params=[[62, "registered"], [62, "missing"]],
    )
    substrate.query_multi.assert_awaited_once_with(storage_keys)


@pytest.mark.anyio
async def test_get_uids_for_hotkeys_on_subnet_deduplicates_and_handles_empty_input() -> None:
    storage_key = FakeStorageKey("0x1")
    substrate = SimpleNamespace(
        create_storage_keys=AsyncMock(return_value=[storage_key]),
        query_multi=AsyncMock(return_value=[(storage_key, 7)]),
    )
    client = SubtensorClient()
    client._subtensor = SimpleNamespace(substrate=substrate)

    assert await client.get_uids_for_hotkeys_on_subnet(["hk", "hk"], netuid=62) == {"hk": 7}
    assert await client.get_uids_for_hotkeys_on_subnet([], netuid=62) == {}
    substrate.create_storage_keys.assert_awaited_once()
    substrate.query_multi.assert_awaited_once()


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
