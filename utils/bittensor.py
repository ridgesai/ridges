from typing import TYPE_CHECKING

from bittensor.core.async_subtensor import AsyncSubtensor
from bittensor_wallet.keypair import Keypair

import api.config as config
import utils.logger as logger

if TYPE_CHECKING:
    from async_substrate_interface import AsyncSubstrateInterface
    from async_substrate_interface.substrate_addons import RetryAsyncSubstrate
    from bittensor.core.async_subtensor import AsyncSubtensor
    from bittensor.utils.balance import Balance


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

    async def get_balance(self, address: str) -> "Balance":
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

    @property
    def substrate(self) -> AsyncSubstrateInterface | RetryAsyncSubstrate:
        """Retrieve the underlying substrate client for direct access to substrate methods.

        Returns
        -------
        AsyncSubtensor | RetryAsyncSubstrate
            The underlying substrate client instance.
        """
        assert self._subtensor is not None, "Subtensor client is not initialized"
        return self._subtensor.substrate


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
