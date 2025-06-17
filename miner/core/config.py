from typing import Optional, Any, TypeVar
from dataclasses import dataclass
from functools import lru_cache
import os

from fiber.chain.chain_utils import load_hotkey_keypair
from fiber.chain.interface import get_substrate, SubstrateInterface
from fiber.chain.metagraph import Metagraph
from fiber.miner.security.nonce_management import NonceManager
import httpx
from pydantic import BaseModel, Field
from substrateinterface import Keypair

class Config(BaseModel):
    """Config for the miner service"""

    # Network and security configuration
    keypair: Keypair
    metagraph: Metagraph
    httpx_client: httpx.AsyncClient
    nonce_manager: Any  # Using Any to avoid Pydantic validation issues with NonceManager
    min_stake_threshold: float = Field(default=1.0)

    class Config:
        arbitrary_types_allowed = True

@lru_cache
def factory_config() -> Config:
    # Initialize security components
    nonce_manager = NonceManager()

    # Load fiber network configuration
    wallet_name = os.getenv("WALLET_NAME", "miner")
    hotkey_name = os.getenv("HOTKEY_NAME", "default")
    netuid = os.getenv("NETUID", "1")
    subtensor_network = os.getenv("SUBTENSOR_NETWORK", "local")
    subtensor_address = os.getenv("SUBTENSOR_ADDRESS", "ws://127.0.0.1:9945")

    load_old_nodes = bool(os.getenv("LOAD_OLD_NODES", True))
    refresh_nodes = os.getenv("REFRESH_NODES", "true").lower() == "true"

    assert netuid is not None, "Must set NETUID env var please!"

    if refresh_nodes:
        substrate = get_substrate(subtensor_network, subtensor_address)
        metagraph = Metagraph(
            substrate=substrate,
            netuid=netuid,
            load_old_nodes=load_old_nodes
        )
    else:
        metagraph = Metagraph(substrate=None, netuid=netuid, load_old_nodes=load_old_nodes)
    
    keypair = load_hotkey_keypair(wallet_name, hotkey_name)

    return Config(
        nonce_manager=nonce_manager,
        keypair=keypair,
        metagraph=metagraph,
        httpx_client=httpx.AsyncClient()
    )