from __future__ import annotations

import asyncio

import pytest

import utils.database as _db
from db.models.internal_flag import InternalFlagName
from queries.internal_flag import (
    add_hotkey_to_blacklist,
    get_internal_flags_parsed,
    remove_hotkey_from_blacklist,
    set_internal_flag,
)


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE internal_flags")
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE internal_flags")


@pytest.mark.anyio
async def test_set_internal_flag_inserts_then_updates() -> None:
    await set_internal_flag(InternalFlagName.VALIDATORS_PAUSED, "true")
    flags = await get_internal_flags_parsed([InternalFlagName.VALIDATORS_PAUSED])
    assert flags[InternalFlagName.VALIDATORS_PAUSED] is True

    await set_internal_flag(InternalFlagName.VALIDATORS_PAUSED, "false")
    flags = await get_internal_flags_parsed([InternalFlagName.VALIDATORS_PAUSED])
    assert flags[InternalFlagName.VALIDATORS_PAUSED] is False


@pytest.mark.anyio
async def test_blacklist_add_and_remove_roundtrip() -> None:
    assert await add_hotkey_to_blacklist("hotkey-a") == ["hotkey-a"]
    assert await add_hotkey_to_blacklist("hotkey-b") == ["hotkey-a", "hotkey-b"]
    # Idempotent add
    assert await add_hotkey_to_blacklist("hotkey-a") == ["hotkey-a", "hotkey-b"]

    flags = await get_internal_flags_parsed([InternalFlagName.BLACKLISTED_VALIDATORS])
    assert flags[InternalFlagName.BLACKLISTED_VALIDATORS] == ["hotkey-a", "hotkey-b"]

    assert await remove_hotkey_from_blacklist("hotkey-a") == ["hotkey-b"]
    # Idempotent remove
    assert await remove_hotkey_from_blacklist("missing") == ["hotkey-b"]


@pytest.mark.anyio
async def test_add_to_blacklist_starts_from_empty_when_flag_row_missing() -> None:
    assert await add_hotkey_to_blacklist("first-hotkey") == ["first-hotkey"]


@pytest.mark.anyio
async def test_concurrent_blacklist_adds_do_not_lose_updates() -> None:
    hotkeys = [f"hotkey-{i}" for i in range(8)]
    await asyncio.gather(*(add_hotkey_to_blacklist(hotkey) for hotkey in hotkeys))

    flags = await get_internal_flags_parsed([InternalFlagName.BLACKLISTED_VALIDATORS])
    assert sorted(flags[InternalFlagName.BLACKLISTED_VALIDATORS]) == sorted(hotkeys)
