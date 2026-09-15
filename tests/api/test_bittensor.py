import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
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

    async def fake_reconnect() -> None:
        client._subtensor = SimpleNamespace(get_metagraph_info=AsyncMock(return_value=metagraph))

    client.reconnect = fake_reconnect

    result = await client.get_subnet_hotkey_info(netuid=62)

    assert result["hk"].uid == 0
    assert result["hk"].emission == 1.0


@pytest.mark.anyio
async def test_concurrent_reconnects_never_overlap(monkeypatch) -> None:
    """The reconnect lock must serialize rebuilds.

    Without it, concurrent callers interleave teardown and setup and can leave the client holding
    a connection that another caller has already closed.
    """
    in_flight = 0
    max_in_flight = 0
    builds = 0

    async def fake_initialize(self, *, timeout: float | None = None) -> None:
        nonlocal in_flight, max_in_flight, builds
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        builds += 1
        await asyncio.sleep(0.01)  # yield, so any overlap is observable
        self._subtensor = SimpleNamespace(close=AsyncMock())
        in_flight -= 1

    monkeypatch.setattr(SubtensorClient, "initialize", fake_initialize)

    client = SubtensorClient()
    client._subtensor = SimpleNamespace(close=AsyncMock())

    await asyncio.gather(*(client.reconnect() for _ in range(5)))

    assert max_in_flight == 1, "rebuilds overlapped; the reconnect lock is not holding"
    assert builds == 5
    assert client._subtensor is not None


@pytest.mark.anyio
async def test_reconnect_survives_a_close_that_hangs(monkeypatch) -> None:
    """A wedged connection can hang on close; that must not block the rebuild."""
    monkeypatch.setattr(ridges_bittensor, "_CLOSE_TIMEOUT_SECONDS", 0.05)

    async def hanging_close():
        await asyncio.sleep(60)

    rebuilt = False

    async def fake_initialize(self, *, timeout: float | None = None) -> None:
        nonlocal rebuilt
        rebuilt = True
        self._subtensor = SimpleNamespace(close=AsyncMock())

    monkeypatch.setattr(SubtensorClient, "initialize", fake_initialize)

    client = SubtensorClient()
    client._subtensor = SimpleNamespace(close=hanging_close)

    await asyncio.wait_for(client.reconnect(), timeout=5)

    assert rebuilt


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
