import asyncio
import gc
import logging
import weakref
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from async_substrate_interface.utils.cache import CachedFetcher
from bittensor.core.async_subtensor import AsyncSubtensor
from bittensor.core.chain_data.metagraph_info import SelectiveMetagraphIndex

import api.config as config
import utils.bittensor as ridges_bittensor
from utils.bittensor import HotkeySubnetInfo, SubtensorClient, SubtensorUnavailableError


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


#
# Connection resilience: timeouts, reconnect
#


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _capture_bittensor_logs() -> list[logging.LogRecord]:
    """Capture utils.bittensor log records for the rest of the test.

    Attached straight to the module logger rather than using caplog: setup_logging() sets
    propagate = False on configured loggers, so caplog's root handler never sees these records.
    """
    logger = logging.getLogger("utils.bittensor")
    handler = _ListHandler()
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)

    def restore() -> None:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    _cleanups.append(restore)
    return handler.records


_cleanups: list = []


@pytest.fixture(autouse=True)
def _restore_log_handlers():
    yield
    while _cleanups:
        _cleanups.pop()()


async def _never_returns(*args, **kwargs):
    await asyncio.sleep(60)


@pytest.mark.anyio
async def test_chain_call_times_out_instead_of_hanging(monkeypatch) -> None:
    """A call that never returns must raise, not block the caller forever.

    This is the core regression: the substrate layer polls response futures in an unbounded loop,
    so without an enforced timeout a wedged websocket hangs the request indefinitely.
    """
    monkeypatch.setattr(config, "SUBTENSOR_TIMEOUT_SECONDS", 0.05)

    client = SubtensorClient()
    client._subtensor = SimpleNamespace(get_metagraph_info=_never_returns)
    client.reconnect = AsyncMock()

    with pytest.raises(SubtensorUnavailableError):
        await asyncio.wait_for(client.get_subnet_hotkey_info(netuid=62), timeout=5)


@pytest.mark.anyio
async def test_timed_out_call_rebuilds_the_connection(monkeypatch) -> None:
    monkeypatch.setattr(config, "SUBTENSOR_TIMEOUT_SECONDS", 0.05)

    client = SubtensorClient()
    client._subtensor = SimpleNamespace(get_metagraph_info=_never_returns)
    client.reconnect = AsyncMock()

    with pytest.raises(SubtensorUnavailableError):
        await client.get_subnet_hotkey_info(netuid=62)

    client.reconnect.assert_awaited_once()


@pytest.mark.anyio
async def test_timeout_is_logged_as_an_error_for_sentry(monkeypatch) -> None:
    """A timed-out chain call must log at ERROR so it surfaces in Sentry."""
    monkeypatch.setattr(config, "SUBTENSOR_TIMEOUT_SECONDS", 0.05)

    client = SubtensorClient()
    client._subtensor = SimpleNamespace(get_metagraph_info=_never_returns)
    client.reconnect = AsyncMock()

    records = _capture_bittensor_logs()

    with pytest.raises(SubtensorUnavailableError):
        await client.get_subnet_hotkey_info(netuid=62)

    errors = [r for r in records if r.levelno == logging.ERROR]
    assert len(errors) >= 1
    assert "get_subnet_hotkey_info" in errors[0].getMessage()
    # exc_info gives Sentry a stack trace rather than a bare message.
    assert errors[0].exc_info is not None


@pytest.mark.anyio
async def test_timed_out_call_retries_once_on_the_new_connection(monkeypatch) -> None:
    monkeypatch.setattr(config, "SUBTENSOR_TIMEOUT_SECONDS", 0.05)
    metagraph = SimpleNamespace(
        hotkeys=["hk"],
        emission=[SimpleNamespace(tao=1.0)],
    )

    client = SubtensorClient()
    client._subtensor = SimpleNamespace(get_metagraph_info=_never_returns)

    async def fake_reconnect(stale) -> None:
        client._subtensor = SimpleNamespace(get_metagraph_info=AsyncMock(return_value=metagraph))

    client.reconnect = fake_reconnect

    result = await client.get_subnet_hotkey_info(netuid=62)

    assert result["hk"].uid == 0
    assert result["hk"].emission == 1.0


@pytest.mark.anyio
async def test_concurrent_timeouts_share_one_recovery(monkeypatch) -> None:
    monkeypatch.setattr(config, "SUBTENSOR_TIMEOUT_SECONDS", 0.01)
    replacement = _Connection()
    factory = _install_connection(monkeypatch, replacement)
    old = _Connection()
    old.get_balance.side_effect = _never_returns
    client = SubtensorClient()
    client._subtensor = old

    results = await asyncio.gather(*(client.get_balance("address") for _ in range(5)))

    assert results == [123] * 5
    assert len(factory) == 1
    old.close.assert_awaited_once()
    replacement.close.assert_not_awaited()
    assert client._reconnect_task is None
    await client.close()


@pytest.mark.anyio
async def test_reconnect_survives_a_close_that_hangs(monkeypatch) -> None:
    """A wedged connection can hang on close; that must not block the rebuild."""
    monkeypatch.setattr(ridges_bittensor, "_CLOSE_TIMEOUT_SECONDS", 0.05)

    async def hanging_close():
        await asyncio.sleep(60)

    replacement = _Connection()
    _install_connection(monkeypatch, replacement)
    client = SubtensorClient()
    old = SimpleNamespace(close=hanging_close)
    client._subtensor = old

    await asyncio.wait_for(client.reconnect(old), timeout=5)

    assert client._subtensor is replacement
    await client.close()


@pytest.mark.anyio
async def test_initialize_dials_configured_address(monkeypatch) -> None:
    created: dict[str, str] = {}

    class FakeAsyncSubtensor:
        def __init__(self, network: str) -> None:
            created["network"] = network
            self.chain_endpoint = network
            self.network = "finney"

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(config, "SUBTENSOR_ADDRESS", "wss://lite.sub.latent.to:443")
    monkeypatch.setattr("utils.bittensor.AsyncSubtensor", FakeAsyncSubtensor)

    client = SubtensorClient()
    await client.initialize()

    assert created["network"] == "wss://lite.sub.latent.to:443"
    assert client._subtensor is not None
    await client.close()


class _Connection:
    def __init__(self):
        self.chain_endpoint = "mock"
        self.network = "mock"
        self.initialize = AsyncMock()
        self.close = AsyncMock()
        self.get_balance = AsyncMock(return_value=123)


def _install_connection(monkeypatch, *connections):
    created = []

    def factory(**kwargs):
        connection = connections[len(created)]
        created.append(connection)
        return connection

    monkeypatch.setattr(ridges_bittensor, "AsyncSubtensor", factory)
    return created


@pytest.mark.anyio
async def test_late_failure_does_not_replace_fresh_connection(monkeypatch):
    replacement = _Connection()
    published = asyncio.Event()
    replacement.initialize.side_effect = lambda: published.set()
    created = _install_connection(monkeypatch, replacement)
    old = _Connection()

    async def old_read(*, address):
        if address == "late":
            await published.wait()
        raise ConnectionError("old connection failed")

    old.get_balance.side_effect = old_read
    client = SubtensorClient()
    client._subtensor = old
    assert await asyncio.gather(client.get_balance("early"), client.get_balance("late")) == [123, 123]
    assert len(created) == 1
    replacement.close.assert_not_awaited()
    await client.close()


@pytest.mark.anyio
async def test_failed_recovery_has_cooldown_and_later_recovers(monkeypatch):
    monkeypatch.setattr(ridges_bittensor, "_RECONNECT_COOLDOWN_SECONDS", 0.02)
    failed, replacement = _Connection(), _Connection()
    failed.initialize.side_effect = ConnectionError("node offline")
    created = _install_connection(monkeypatch, failed, replacement)
    old = _Connection()
    old.get_balance.side_effect = ConnectionError("connection lost")
    client = SubtensorClient()
    client._subtensor = old

    results = await asyncio.gather(*(client.get_balance("address") for _ in range(5)), return_exceptions=True)
    assert all(isinstance(result, SubtensorUnavailableError) for result in results)
    assert len(created) == 1
    assert client._subtensor is None
    failed.close.assert_awaited_once()
    with pytest.raises(SubtensorUnavailableError):
        await client.get_balance("address")
    assert len(created) == 1
    await asyncio.sleep(0.025)
    assert await client.get_balance("address") == 123
    assert len(created) == 2
    await client.close()


@pytest.mark.anyio
async def test_cancelling_triggering_request_does_not_cancel_recovery(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    replacement = _Connection()

    async def initialize():
        started.set()
        await release.wait()

    replacement.initialize.side_effect = initialize
    created = _install_connection(monkeypatch, replacement)
    old = _Connection()
    old.get_balance.side_effect = ConnectionError("disconnected")
    client = SubtensorClient()
    client._subtensor = old
    caller = asyncio.create_task(client.get_balance("address"))
    await asyncio.wait_for(started.wait(), 1)
    recovery = client._reconnect_task
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert recovery is not None and not recovery.done()
    other = asyncio.create_task(client.get_balance("address"))
    release.set()
    assert await other == 123
    assert len(created) == 1
    assert client._reconnect_task is None
    await client.close()


@pytest.mark.anyio
async def test_recovery_wait_timeout_keeps_single_owner(monkeypatch):
    monkeypatch.setattr(ridges_bittensor, "_RECONNECT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(ridges_bittensor, "_CLOSE_TIMEOUT_SECONDS", 0.005)
    release = asyncio.Event()
    replacement = _Connection()

    async def slow_cancellation():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    replacement.initialize.side_effect = slow_cancellation
    created = _install_connection(monkeypatch, replacement)
    client = SubtensorClient()
    try:
        for _ in range(2):
            with pytest.raises(SubtensorUnavailableError, match="still in progress"):
                await asyncio.wait_for(client.get_balance("address"), 1)
        assert len(created) == 1
        recovery = client._reconnect_task
        assert recovery is not None and not recovery.done()
    finally:
        release.set()
        if client._reconnect_task is not None:
            await client._reconnect_task
        await client.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        ConnectionError("offline"),
        ValueError("invalid initialization"),
        asyncio.CancelledError(),
        pytest.param(_never_returns, id="timeout"),
    ],
)
async def test_failed_initialization_cleans_up_candidate(monkeypatch, failure):
    candidate = _Connection()
    candidate.initialize.side_effect = failure
    _install_connection(monkeypatch, candidate)
    client = SubtensorClient()
    expected = ValueError if isinstance(failure, ValueError) else SubtensorUnavailableError
    with pytest.raises(expected):
        await client.initialize(timeout=0.01)
    candidate.close.assert_awaited_once()
    assert client._subtensor is None
    assert client._reconnect_task is None


@pytest.mark.anyio
async def test_failed_startup_cannot_publish_a_late_connection(monkeypatch):
    monkeypatch.setattr(config, "SUBTENSOR_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(ridges_bittensor, "_CLOSE_TIMEOUT_SECONDS", 0.005)
    release = asyncio.Event()
    candidate = _Connection()

    async def slow_cancellation():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    candidate.initialize.side_effect = slow_cancellation
    _install_connection(monkeypatch, candidate)
    client = SubtensorClient()
    try:
        with pytest.raises(SubtensorUnavailableError):
            await asyncio.wait_for(client.initialize(), 1)
        assert client._stopping
        assert client._subtensor is None
    finally:
        release.set()
        if client._reconnect_task is not None:
            await asyncio.gather(client._reconnect_task, return_exceptions=True)
    assert client._subtensor is None
    assert client._reconnect_task is None
    candidate.close.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("cancel_startup", [False, True])
async def test_shutdown_during_initialization_never_publishes_candidate(monkeypatch, cancel_startup):
    entered, release = asyncio.Event(), asyncio.Event()
    candidate = _Connection()

    async def initialize():
        entered.set()
        await release.wait()

    candidate.initialize.side_effect = initialize
    _install_connection(monkeypatch, candidate)
    client = SubtensorClient()
    startup = asyncio.create_task(client.initialize())
    await asyncio.wait_for(entered.wait(), 1)
    if cancel_startup:
        startup.cancel()
        shutdown = None
    else:
        shutdown = asyncio.create_task(client.close())
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError if cancel_startup else SubtensorUnavailableError):
        await startup
    if shutdown is not None:
        await shutdown
    assert client._subtensor is None
    assert client._reconnect_task is None
    candidate.close.assert_awaited_once()
    with pytest.raises(SubtensorUnavailableError, match="shutting down"):
        await client.get_balance("address")


@pytest.mark.anyio
async def test_shared_lookup_timeout_recovers_cancelled_peers_and_producer(monkeypatch):
    """Exercise the pinned dependency's real shared-future cancellation behavior."""
    monkeypatch.setattr(config, "SUBTENSOR_TIMEOUT_SECONDS", 1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def lookup(address):
        entered.set()
        await release.wait()
        return 456

    fetcher = CachedFetcher(max_size=2, method=lookup)

    async def shared_read(*, address):
        return await fetcher(address)

    old = _Connection()
    old.get_balance.side_effect = shared_read
    old.close.side_effect = lambda: release.set()
    replacement = _Connection()
    created = _install_connection(monkeypatch, replacement)
    client = SubtensorClient()
    client._subtensor = old
    producer = asyncio.create_task(client.get_balance("same"))
    await asyncio.wait_for(entered.wait(), 1)
    timed = asyncio.create_task(client._call("lookup", lambda conn: conn.get_balance(address="same"), timeout=0.01))
    peer = asyncio.create_task(client.get_balance("same"))
    results = await asyncio.wait_for(asyncio.gather(producer, timed, peer), 2)
    assert results == [123, 123, 123]
    assert len(created) == 1
    old.close.assert_awaited_once()
    await client.close()


@pytest.mark.anyio
async def test_real_caller_cancellation_propagates_without_reconnect(monkeypatch):
    entered = asyncio.Event()

    async def read(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    client = SubtensorClient()
    old = _Connection()
    old.get_balance.side_effect = read
    client._subtensor = old
    caller = asyncio.create_task(client.get_balance("address"))
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert client._subtensor is old
    assert client._reconnect_task is None
    await client.close()


@pytest.mark.anyio
async def test_programming_error_is_not_retried():
    client = SubtensorClient()
    client._subtensor = _Connection()
    client._subtensor.get_balance.side_effect = ValueError("bad decode")
    with pytest.raises(ValueError, match="bad decode"):
        await client.get_balance("address")
    client._subtensor.get_balance.assert_awaited_once()
    assert client._reconnect_task is None
    await client.close()


@pytest.mark.anyio
async def test_repeated_recovery_releases_old_clients_and_completed_tasks(monkeypatch):
    # pytest's captured exception records themselves retain traceback locals; exclude that artifact.
    monkeypatch.setattr(ridges_bittensor.logger, "disabled", True)
    live = weakref.WeakSet()

    def factory(**kwargs):
        connection = _Connection()
        live.add(connection)
        return connection

    monkeypatch.setattr(ridges_bittensor, "AsyncSubtensor", factory)
    client = SubtensorClient()
    await client.initialize()
    for _ in range(10):
        client._subtensor.get_balance.side_effect = ConnectionError("lost")
        assert await client.get_balance("address") == 123
        assert client._reconnect_task is None
        gc.collect()
        assert len(live) == 1
    await client.close()
    gc.collect()
    assert len(live) == 0


@pytest.mark.anyio
async def test_historical_owner_hash_does_not_retain_closed_clients_in_sdk_cache():
    live = weakref.WeakSet()
    cache_size = AsyncSubtensor._get_block_hash.cache_info().currsize
    for _ in range(12):
        chain = AsyncSubtensor(network="ws://unused")
        live.add(chain)
        chain.substrate.get_block_hash = AsyncMock()
        chain.substrate.query = AsyncMock(return_value=SimpleNamespace(value="owner"))
        chain.does_hotkey_exist = AsyncMock(return_value=True)
        client = SubtensorClient()
        client._subtensor = chain
        try:
            assert await client.get_hotkey_owner("hotkey", block_hash="0xpayment") == "owner"
            chain.substrate.get_block_hash.assert_not_awaited()
            assert chain.substrate.query.call_args.kwargs["block_hash"] == "0xpayment"
        finally:
            await client.close()
        del chain, client

    gc.collect()
    assert len(live) == 0
    assert AsyncSubtensor._get_block_hash.cache_info().currsize == cache_size
