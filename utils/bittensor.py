import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from bittensor.core.async_subtensor import AsyncSubtensor
from bittensor.core.chain_data.metagraph_info import SelectiveMetagraphIndex
from bittensor.utils.balance import Balance
from bittensor_wallet.keypair import Keypair
from websockets.exceptions import WebSocketException
from websockets.protocol import State

import api.config as config

if TYPE_CHECKING:
    from bittensor.core.chain_data.neuron_info_lite import NeuronInfoLite
    from bittensor.core.types import BlockInfo

logger = logging.getLogger(__name__)

# Rebuild must not inherit SUBTENSOR_TIMEOUT_SECONDS: after a hung RPC we drop the socket
# quickly rather than waiting another full request budget.
_RECONNECT_TIMEOUT_SECONDS = 5
_CLOSE_TIMEOUT_SECONDS = 2
_RECONNECT_COOLDOWN_SECONDS = 5


class SubtensorUnavailableError(RuntimeError):
    """A chain call could not be completed because the connection is unavailable or timed out.

    Reports an unavailable connection or a read that failed despite one recovery attempt.
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
        self._reconnect_task: asyncio.Task[None] | None = None
        self._retry_after = 0.0
        self._stopping = False

    async def initialize(self, *, timeout: float | None = None) -> None:
        """Initialize connection to the Subtensor network.

        ``network`` is the configured websocket URL so the process dials that node instead of
        bittensor's named-network default (which ignored ``SUBTENSOR_ADDRESS``). Idle sockets use
        the library default shutdown timer and reopen on the next RPC.

        First connect and per-request RPCs use ``SUBTENSOR_TIMEOUT_SECONDS``. Rebuilds pass a
        short reconnect timeout so a dead socket is replaced quickly.
        """
        try:
            await self.reconnect(None, timeout=timeout if timeout is not None else config.SUBTENSOR_TIMEOUT_SECONDS)
        except (Exception, asyncio.CancelledError):
            await self.close()
            raise

    async def _connect(self, timeout: float) -> AsyncSubtensor:
        if self._stopping:
            raise SubtensorUnavailableError("Subtensor client is shutting down")

        subtensor = AsyncSubtensor(network=config.SUBTENSOR_ADDRESS)
        try:
            await asyncio.wait_for(subtensor.initialize(), timeout=timeout)
            if self._stopping:
                raise SubtensorUnavailableError("Subtensor client is shutting down")

        except (Exception, asyncio.CancelledError) as exc:
            await self._close_client(subtensor)
            if isinstance(exc, (OSError, WebSocketException)):
                raise SubtensorUnavailableError("Subtensor connection could not be initialized") from exc
            raise
        logger.info(
            f"Subtensor connection initialized chain_endpoint={subtensor.chain_endpoint} network={subtensor.network}"
        )
        return subtensor

    async def _close_client(self, subtensor: AsyncSubtensor) -> None:
        manager = getattr(getattr(subtensor, "substrate", None), "ws", None)
        connection = getattr(manager, "ws", None)
        handler = getattr(manager, "_send_recv_task", None)

        # Cancel pending futures to prevent resource leaks before handler shutdown.
        for response in tuple(getattr(manager, "_received", {}).values()):
            if not response.done():
                response.cancel()

        failed = False
        try:
            await asyncio.wait_for(subtensor.close(), timeout=_CLOSE_TIMEOUT_SECONDS)
        except Exception:
            failed = True
            logger.warning("Subtensor cleanup failed or timed out", exc_info=True)
        finally:
            # Ensure existing socket is tracked since shutdown can be skipped
            current = getattr(manager, "ws", None)
            sockets = {socket for socket in (connection, current) if socket is not None}
            needs_fallback = (
                failed
                or any(socket.state != State.CLOSED for socket in sockets)
                or (handler is not None and not handler.done())
            )
            if manager is not None and needs_fallback:
                try:
                    # Abort sockets first to avoid hanging connections.
                    for socket in sockets:
                        if socket.state != State.CLOSED:
                            socket.transport.abort()
                    await asyncio.wait_for(manager.shutdown(), timeout=_CLOSE_TIMEOUT_SECONDS)

                except Exception:
                    logger.warning("Fallback Subtensor shutdown failed or timed out", exc_info=True)

                finally:
                    current = getattr(manager, "ws", None)
                    if current is not None:
                        sockets.add(current)
                    for socket in sockets:
                        if socket.state != State.CLOSED:
                            socket.transport.abort()

    async def close(self) -> None:
        """Close connection to the Subtensor network."""
        async with self._reconnect_lock:
            self._stopping = True
            old, self._subtensor = self._subtensor, None
            recovery = self._reconnect_task

        if recovery is not None:
            try:
                await self._wait_for_recovery(recovery, config.SUBTENSOR_TIMEOUT_SECONDS)
            except Exception:
                logger.warning("Subtensor recovery did not finish normally during shutdown", exc_info=True)

        if old is not None:
            await self._close_client(old)
        logger.info("Subtensor client stopped")

    async def reconnect(self, stale: AsyncSubtensor | None, *, timeout: float | None = None) -> None:
        """Share one close-first recovery, replacing only the client that actually failed."""
        bound = timeout if timeout is not None else _RECONNECT_TIMEOUT_SECONDS
        async with self._reconnect_lock:
            if self._stopping:
                raise SubtensorUnavailableError("Subtensor client is shutting down")

            recovery = self._reconnect_task
            if recovery is None or recovery.done():
                if self._subtensor is not None and self._subtensor is not stale:
                    return

                if asyncio.get_running_loop().time() < self._retry_after:
                    raise SubtensorUnavailableError("Subtensor recovery failed; please retry shortly")

                old, self._subtensor = self._subtensor, None
                recovery = asyncio.create_task(self._recover(old, bound))
                self._reconnect_task = recovery
                recovery.add_done_callback(self._recovery_finished)
        await self._wait_for_recovery(recovery, bound)

    async def _recover(self, old: AsyncSubtensor | None, timeout: float) -> None:
        try:
            if old is not None:
                await self._close_client(old)

            self._subtensor = await self._connect(timeout)
            self._retry_after = 0.0
        except (Exception, asyncio.CancelledError):
            self._retry_after = asyncio.get_running_loop().time() + _RECONNECT_COOLDOWN_SECONDS
            if not self._stopping:
                logger.error("Subtensor recovery failed", exc_info=True)
            raise

    def _recovery_finished(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

        if self._reconnect_task is task:
            self._reconnect_task = None

    async def _wait_for_recovery(self, task: asyncio.Task[None], timeout: float) -> None:
        try:
            # Budget for closing the old client, connecting, and cleaning up a failed candidate.
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout + 2 * _CLOSE_TIMEOUT_SECONDS)

        except TimeoutError as exc:
            raise SubtensorUnavailableError("Subtensor recovery is still in progress") from exc

        except asyncio.CancelledError as exc:
            if asyncio.current_task().cancelling():
                raise
            raise SubtensorUnavailableError("Subtensor recovery was interrupted") from exc

    async def _call(
        self,
        name: str,
        coro_factory: Callable[[AsyncSubtensor], Awaitable[Any]],
        timeout: float | None = None,
    ) -> Any:
        """Run a read with cooperative cancellation and at most one retry after shared recovery."""
        if self._stopping:
            raise SubtensorUnavailableError("Subtensor client is shutting down")

        if self._subtensor is None:
            await self.reconnect(None)

        subtensor = self._subtensor
        effective_timeout = timeout if timeout is not None else config.SUBTENSOR_TIMEOUT_SECONDS
        for attempt in range(2):
            if subtensor is None or self._stopping:
                raise SubtensorUnavailableError("Subtensor connection is unavailable")

            try:
                return await asyncio.wait_for(coro_factory(subtensor), timeout=effective_timeout)
            except (OSError, WebSocketException, asyncio.InvalidStateError, asyncio.CancelledError) as exc:
                # Only handle cancellation if this task was explicitly cancelled.
                if isinstance(exc, asyncio.CancelledError) and asyncio.current_task().cancelling():
                    raise

                reason = f"timed out after {effective_timeout}s" if isinstance(exc, TimeoutError) else repr(exc)
                message = f"Subtensor call {name} failed: {reason}"
                logger.error(message, exc_info=exc)
                if attempt:
                    raise SubtensorUnavailableError(f"{message} (after reconnect)") from exc

            await self.reconnect(subtensor)
            subtensor = self._subtensor

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

    async def get_hotkey_owner(
        self, hotkey: str, block: int | None = None, *, block_hash: str | None = None
    ) -> str | None:
        """Retrieve the owner at a specific block, or latest if no block is supplied.

        Parameters
        ----------
        hotkey : str
            Hotkey for which to retrieve the owner.
        block : int | None, optional
            Block number at which to retrieve the owner, by default None.
        block_hash : str | None, optional
            Prefer a known hash to avoid the SDK's block-number cache retaining the client.

        Returns
        -------
        str | None
            The owner of the specified hotkey, or None if not found.
        """
        return await self._call(
            "get_hotkey_owner",
            lambda subtensor: subtensor.get_hotkey_owner(hotkey_ss58=hotkey, block=block, block_hash=block_hash),
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

    async def get_neurons_lite(self, netuid: int) -> list["NeuronInfoLite"]:
        """Retrieve all neurons (lite) for a given subnet."""
        assert self._subtensor is not None, "Subtensor client is not initialized"
        return await self._subtensor.neurons_lite(netuid=netuid)

    async def get_blocks_until_next_epoch(self, netuid: int) -> int | None:
        """Return the number of blocks until the next epoch for a given subnet."""
        assert self._subtensor is not None, "Subtensor client is not initialized"
        return await self._subtensor.blocks_until_next_epoch(netuid=netuid)

    async def get_current_block(self) -> int:
        """Return the current block number."""
        assert self._subtensor is not None, "Subtensor client is not initialized"
        return await self._subtensor.get_current_block()


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
