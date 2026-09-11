import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from bittensor.core.async_subtensor import AsyncSubtensor
from bittensor.core.chain_data.metagraph_info import SelectiveMetagraphIndex
from bittensor.utils.balance import Balance
from bittensor_wallet.keypair import Keypair

import api.config as config

if TYPE_CHECKING:
    from bittensor.core.types import BlockInfo

logger = logging.getLogger(__name__)


class SubtensorUnavailableError(RuntimeError):
    """A chain call could not be completed because the connection is unavailable or timed out.

    Raised instead of hanging. The underlying substrate client polls response futures in an
    unbounded loop, so without an enforced timeout a wedged websocket blocks the caller forever.
    """


@dataclass(frozen=True, slots=True)
class AlphaStakeAvailability:
    """Alpha that can be burned from one stake position at a single chain head."""

    block_hash: str
    position_rao: int
    total_rao: int
    locked_rao: int
    available_rao: int
    burnable_rao: int


@dataclass(frozen=True, slots=True)
class HotkeySubnetInfo:
    uid: int
    emission: float | None


class SubtensorClient:
    """Subtensor client for interacting with the Subtensor network. Provides methods to check hotkey registration, get hotkey owner, and retrieve wallet balance.

    This client is designed to be initialized once and used throughout the application. It maintains a connection to the Subtensor network and provides convenient methods for common operations.

    Example usage:
        subtensor_client = SubtensorClient()
        await subtensor_client.initialize()

        hotkey = "5F3sa2TJAWMqDhXG6jhV4N8ko9rLxwYQqvM1uYjZqLh9VJ"
        is_registered = await subtensor_client.is_hotkey_registered(hotkey)
        print(f"Is hotkey registered? {is_registered}")

        owner = await subtensor_client.get_hotkey_owner(hotkey)
        print(f"Hotkey owner: {owner}")

        balance = await subtensor_client.get_balance(owner)
        print(f"Wallet balance: {balance}")

        await subtensor_client.close()
    """

    def __init__(self) -> None:
        self._subtensor: AsyncSubtensor | None = None
        self._reconnect_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize connection to the Subtensor network.

        ``fallback_endpoints`` makes bittensor build a ``RetryAsyncSubstrate`` with failover and backoff instead of a bare ``AsyncSubstrateInterface``.

        ``websocket_shutdown_timer=None`` keeps the socket open instead of closing it a few seconds after the last response. The library documents this for long-running processes; the keepalive loop is what maintains liveness from here on.
        """
        subtensor = AsyncSubtensor(
            network=config.SUBTENSOR_NETWORK,
            fallback_endpoints=[config.SUBTENSOR_ADDRESS] if config.SUBTENSOR_ADDRESS else None,
            websocket_shutdown_timer=None,
        )
        try:
            await asyncio.wait_for(subtensor.initialize(), timeout=config.SUBTENSOR_CALL_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            with contextlib.suppress(Exception):
                await subtensor.close()
            raise SubtensorUnavailableError(
                f"Subtensor connection timed out after {config.SUBTENSOR_CALL_TIMEOUT_SECONDS}s"
            ) from exc
        self._subtensor = subtensor
        logger.info("Subtensor connection initialized")

    async def close(self) -> None:
        """Close connection to the Subtensor network."""
        if self._subtensor:
            await self._subtensor.close()
            self._subtensor = None
            logger.info("Subtensor connection closed")

    async def reconnect(self) -> None:
        """Tear down the current connection and build a fresh one.

        Recovers the permanently wedged state the substrate library can reach after exhausting its
        internal reconnect attempts, where the socket may still report OPEN but no response future
        will ever resolve again.

        Held under a lock so that concurrent callers collapse into a single rebuild rather than
        racing to replace each other's connections.
        """
        async with self._reconnect_lock:
            old, self._subtensor = self._subtensor, None
            if old is not None:
                # A wedged connection can hang on close too, so bound it and move on regardless.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(old.close(), timeout=config.SUBTENSOR_PING_TIMEOUT_SECONDS)
            await self.initialize()

    async def _call(
        self,
        name: str,
        coro_factory: Callable[[AsyncSubtensor], Awaitable[Any]],
        timeout: float | None = None,
    ) -> Any:
        """Run one chain call under a hard timeout.

        Every chain method goes through here. Without it a stalled websocket blocks the awaiting
        request indefinitely, since the substrate layer applies no timeout of its own.
        """
        subtensor = self._subtensor
        assert subtensor is not None, "Subtensor client is not initialized"
        effective_timeout = timeout if timeout is not None else config.SUBTENSOR_CALL_TIMEOUT_SECONDS
        try:
            return await asyncio.wait_for(coro_factory(subtensor), timeout=effective_timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            message = f"Subtensor call {name} timed out after {effective_timeout}s"
            # logger.error so this surfaces in Sentry: a timeout here means the websocket is
            # wedged and requests are being dropped, which is exactly what we want alerting on.
            logger.error(message, exc_info=exc)
            raise SubtensorUnavailableError(message) from exc

    async def ping(self) -> bool:
        """Return whether the connection can still complete a cheap chain read."""
        try:
            await self._call(
                "ping",
                lambda subtensor: subtensor.substrate.get_chain_head(),
                timeout=config.SUBTENSOR_PING_TIMEOUT_SECONDS,
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Subtensor ping failed: {type(e).__name__}: {e}")
            return False

    async def is_hotkey_registered(self, hotkey: str) -> bool:
        """Check if provided hotkey is registered on the
        configured subnet.

        Parameters
        ----------
        hotkey : str
            Hotkey to check if it is registered on the subnet.

        Returns
        -------
        bool
            Returns True if the hotkey is registered on the subnet, False otherwise.
        """
        logger.info(f"Checking if hotkey {hotkey} is registered on subnet {config.NETUID}...")
        result = await self._call(
            "is_hotkey_registered",
            lambda subtensor: subtensor.is_hotkey_registered(hotkey_ss58=hotkey, netuid=config.NETUID),
        )
        logger.info(f"Hotkey {hotkey} is {'registered' if result else 'not registered'} on subnet {config.NETUID}")
        return result

    async def get_subnet_hotkey_info(
        self,
        *,
        netuid: int = config.NETUID,
    ) -> dict[str, HotkeySubnetInfo]:
        """Return every subnet hotkey's UID and emission from one selective metagraph call."""

        metagraph = await self._call(
            "get_subnet_hotkey_info",
            lambda subtensor: subtensor.get_metagraph_info(
                netuid=netuid,
                selected_indices=[
                    SelectiveMetagraphIndex.Hotkeys,
                    SelectiveMetagraphIndex.Emission,
                ],
            ),
        )
        if metagraph is None or metagraph.hotkeys is None:
            raise RuntimeError(f"Could not retrieve hotkeys for subnet {netuid}")

        emissions = metagraph.emission or []
        result = {
            hotkey: HotkeySubnetInfo(
                uid=uid,
                emission=float(emissions[uid].tao) if uid < len(emissions) else None,
            )
            for uid, hotkey in enumerate(metagraph.hotkeys)
        }
        logger.info(f"Fetched UID and emission data for {len(result)} hotkeys on subnet {netuid}")
        return result

    async def get_hotkey_owner(self, hotkey: str, block: int | None = None) -> str | None:
        """Retrieve the owner of a given hotkey at a specific block (or latest if block is None).

        Parameters
        ----------
        hotkey : str
            Hotkey for which to retrieve the owner.
        block : int | None, optional
            Block number at which to retrieve the owner, by default None.

        Returns
        -------
        str | None
            The owner of the specified hotkey, or None if not found.
        """
        return await self._call(
            "get_hotkey_owner",
            lambda subtensor: subtensor.get_hotkey_owner(hotkey_ss58=hotkey, block=block),
        )

    async def get_balance(self, address: str) -> Balance:
        """Retrieve the balance of a wallet with a specific
        address.

        Parameters
        ----------
        address : str
            Wallet address.

        Returns
        -------
        Balance
            Wallet balance object.
        """
        return await self._call("get_balance", lambda subtensor: subtensor.get_balance(address=address))

    async def get_alpha_stake_availability(
        self,
        coldkey: str,
        hotkey: str,
        netuid: int,
    ) -> AlphaStakeAvailability:
        """Return burnable alpha for an exact ``(coldkey, hotkey, netuid)`` position.

        Subtensor's alpha lock applies to the coldkey's total stake on a subnet,
        while ``burn_alpha`` is capped by the selected hotkey position. All values
        are therefore read at one chain head and both limits are applied.

        Parameters
        ----------
        coldkey : str
            Coldkey ss58 address which owns the stake.
        hotkey : str
            Hotkey ss58 address identifying the stake position to burn from.
        netuid : int
            Subnet whose alpha will be burned.

        Returns
        -------
        AlphaStakeAvailability
            Position, subnet-wide, locked, available, and burnable amounts in rao.
        """
        block_hash = await self._call(
            "get_alpha_stake_availability.get_chain_head",
            lambda subtensor: subtensor.substrate.get_chain_head(),
        )
        position = await self._call(
            "get_alpha_stake_availability.get_stake",
            lambda subtensor: subtensor.get_stake(
                coldkey_ss58=coldkey,
                hotkey_ss58=hotkey,
                netuid=netuid,
                block_hash=block_hash,
            ),
        )
        availability = await self._call(
            "get_alpha_stake_availability.get_stake_availability_for_coldkeys",
            lambda subtensor: subtensor.get_stake_availability_for_coldkeys(
                [coldkey],
                netuids=[netuid],
                block_hash=block_hash,
            ),
        )

        if not isinstance(availability, dict):
            raise ValueError("Invalid stake availability response")
        by_netuid = availability.get(coldkey)
        if not isinstance(by_netuid, dict):
            raise ValueError(f"Missing stake availability for {coldkey}")
        raw = by_netuid.get(netuid)
        if raw is None:
            total_rao = 0
            locked_rao = 0
            available_rao = 0
        elif not isinstance(raw, dict) or not {"total", "locked", "available"}.issubset(raw):
            raise ValueError(f"Invalid stake availability for {coldkey} on subnet {netuid}")
        else:
            total_rao = int(raw["total"])
            locked_rao = int(raw["locked"])
            available_rao = int(raw["available"])

        position_rao = position.rao

        return AlphaStakeAvailability(
            block_hash=block_hash,
            position_rao=position_rao,
            total_rao=total_rao,
            locked_rao=locked_rao,
            available_rao=available_rao,
            burnable_rao=min(position_rao, available_rao),
        )

    async def get_alpha_price_tao(self, netuid: int, block: int | None = None) -> float:
        """Return the current alpha price (in TAO) for a subnet.

        Parameters
        ----------
        block : int | None, optional
            Block at which to read, by default latest.

        Returns
        -------
        float
            Alpha price denominated in TAO.
        """
        price = await self._call(
            "get_alpha_price_tao",
            lambda subtensor: subtensor.get_subnet_price(netuid=netuid, block=block),
        )
        return float(price.tao)

    async def get_block(self, block_hash: str) -> dict | None:
        """Retrieve a block by its hash.

        Parameters
        ----------
        block_hash : str
            The hash of the block to retrieve.

        Returns
        -------
        dict | None
            The block data, or None if not found.
        """
        return await self._call(
            "get_block",
            lambda subtensor: subtensor.substrate.get_block(block_hash=block_hash),
        )

    async def get_block_info(self, block_hash: str) -> "BlockInfo | None":
        """Retrieve decoded block information by its hash."""
        return await self._call(
            "get_block_info",
            lambda subtensor: subtensor.get_block_info(block_hash=block_hash),
        )

    async def get_events(self, block_hash: str) -> list:
        """Retrieve events for a given block hash.

        Parameters
        ----------
        block_hash : str
            The hash of the block whose events to retrieve.

        Returns
        -------
        list
            List of events in the block.
        """
        return await self._call(
            "get_events",
            lambda subtensor: subtensor.substrate.get_events(block_hash=block_hash),
        )

    async def get_emission(self, hotkey: str) -> float:
        """Retrieve the emission for a given hotkey on the configured subnet.

        Parameters
        ----------
        hotkey : str
            Hotkey for which to retrieve the emission.

        Returns
        -------
        float
            Emission value in TAO, or 0.0 if the hotkey is not registered.
        """
        neuron = await self._call(
            "get_emission",
            lambda subtensor: subtensor.get_neuron_for_pubkey_and_subnet(hotkey_ss58=hotkey, netuid=config.NETUID),
        )
        if neuron is None or neuron.is_null:
            return 0.0
        return float(neuron.emission)


def validate_signed_timestamp(timestamp: int, signed_timestamp: str, hotkey: str) -> bool:
    try:
        keypair = Keypair(ss58_address=hotkey)
        return keypair.verify(str(timestamp), bytes.fromhex(signed_timestamp))
    except Exception as e:
        logger.warning(
            f"Error in validate_signed_timestamp(timestamp={timestamp}, signed_timestamp={signed_timestamp}, hotkey={hotkey}): {e}"
        )
        return False


# Module-level singleton — connected only after initialize() is called from the FastAPI lifespan
subtensor_client = SubtensorClient()
