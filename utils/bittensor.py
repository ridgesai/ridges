import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bittensor.core.async_subtensor import AsyncSubtensor
from bittensor.utils.balance import Balance
from bittensor_wallet.keypair import Keypair

import api.config as config

if TYPE_CHECKING:
    from bittensor.core.types import BlockInfo

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AlphaStakeAvailability:
    """Alpha that can be burned from one stake position at a single chain head."""

    block_hash: str
    position_rao: int
    total_rao: int
    locked_rao: int
    available_rao: int
    burnable_rao: int


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

    async def initialize(self) -> None:
        """Initialize connection to the Subtensor network."""
        self._subtensor = AsyncSubtensor(network=config.SUBTENSOR_NETWORK)
        await self._subtensor.initialize()
        logger.info("Subtensor connection initialized")

    async def close(self) -> None:
        """Close connection to the Subtensor network."""
        if self._subtensor:
            await self._subtensor.close()
            self._subtensor = None
            logger.info("Subtensor connection closed")

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
        assert self._subtensor is not None, "Subtensor client is not initialized"
        logger.info(f"Checking if hotkey {hotkey} is registered on subnet {config.NETUID}...")
        result = await self._subtensor.is_hotkey_registered(hotkey_ss58=hotkey, netuid=config.NETUID)
        logger.info(f"Hotkey {hotkey} is {'registered' if result else 'not registered'} on subnet {config.NETUID}")
        return result

    async def get_uids_for_hotkeys_on_subnet(
        self,
        hotkeys: Sequence[str],
        *,
        netuid: int = config.NETUID,
    ) -> dict[str, int | None]:
        """Return each hotkey's UID using one exact-key storage query."""

        unique_hotkeys = list(dict.fromkeys(hotkeys))
        if not unique_hotkeys:
            return {}

        assert self._subtensor is not None, "Subtensor client is not initialized"
        storage_keys = await self._subtensor.substrate.create_storage_keys(
            pallet="SubtensorModule",
            storage_function="Uids",
            params=[[netuid, hotkey] for hotkey in unique_hotkeys],
        )
        hotkey_by_storage_key = {
            storage_key.to_hex(): hotkey for storage_key, hotkey in zip(storage_keys, unique_hotkeys, strict=True)
        }
        uids: dict[str, int | None] = {hotkey: None for hotkey in unique_hotkeys}

        for storage_key, uid in await self._subtensor.substrate.query_multi(storage_keys):
            hotkey = hotkey_by_storage_key[storage_key.to_hex()]
            uids[hotkey] = None if uid is None else int(uid)

        logger.info(
            f"Found {sum(uid is not None for uid in uids.values())}/{len(uids)} hotkeys registered on subnet {netuid}"
        )
        return uids

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
        assert self._subtensor is not None, "Subtensor client is not initialized"
        return await self._subtensor.get_hotkey_owner(hotkey_ss58=hotkey, block=block)

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
        assert self._subtensor is not None, "Subtensor client is not initialized"
        return await self._subtensor.get_balance(address=address)

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
        assert self._subtensor is not None, "Subtensor client is not initialized"

        block_hash = await self._subtensor.substrate.get_chain_head()
        position = await self._subtensor.get_stake(
            coldkey_ss58=coldkey,
            hotkey_ss58=hotkey,
            netuid=netuid,
            block_hash=block_hash,
        )
        availability = await self._subtensor.get_stake_availability_for_coldkeys(
            [coldkey],
            netuids=[netuid],
            block_hash=block_hash,
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
        assert self._subtensor is not None, "Subtensor client is not initialized"
        price = await self._subtensor.get_subnet_price(netuid=netuid, block=block)
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
        assert self._subtensor is not None, "Subtensor client is not initialized"
        return await self._subtensor.substrate.get_block(block_hash=block_hash)

    async def get_block_info(self, block_hash: str) -> "BlockInfo | None":
        """Retrieve decoded block information by its hash."""
        assert self._subtensor is not None, "Subtensor client is not initialized"
        return await self._subtensor.get_block_info(block_hash=block_hash)

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
        assert self._subtensor is not None, "Subtensor client is not initialized"
        return await self._subtensor.substrate.get_events(block_hash=block_hash)

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
        assert self._subtensor is not None, "Subtensor client is not initialized"
        neuron = await self._subtensor.get_neuron_for_pubkey_and_subnet(hotkey_ss58=hotkey, netuid=config.NETUID)
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
