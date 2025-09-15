# Handles validator start and stop, including creating websocket connection, cleanup ops and version checks before anything happens, etc
from typing import Optional, Any
from enum import Enum

import websockets

import asyncio

from new_validator.connection import ConnectionManager
class ValidatorStatus(Enum):
    STARTING = 'starting'
    IDLING = 'idling'
    EVALUATING = 'evaluating'
    SHUTTING_DOWN = 'shutting_down'

from validator.config import WALLET_NAME, HOTKEY_NAME

from fiber.chain.chain_utils import load_hotkey_keypair
validator_hotkey = load_hotkey_keypair(WALLET_NAME, HOTKEY_NAME)

class RidgesValidator:
    connection_manager: Optional['ConnectionManager'] # TODO: typing for socket manager too
    sandbox_manager: Optional[Any] # TODO: Add typing once sandbox mgr is created
    status: ValidatorStatus

    def __init__(self, hotkey: str) -> None:
        self.sandbox_manager = None
        self.connection_manager = ConnectionManager(hotkey=hotkey)
        self.status = ValidatorStatus.STARTING

        self.start()

    async def start(self):
        self.connection_manager.create_connections()
        # TODO: Init sbox manager
        return
        
    async def shutdown(self):
        # TODO

        self.connection_manager.close_connections()

        pass

async def main():
    """
    This starts up the validator websocket, which connects to the Ridges platform 
    It receives and sends events like new agents to evaluate, eval status, scores, etc
    """
    validator = RidgesValidator()
    try:
        await validator.start()
    except KeyboardInterrupt:
        await validator.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass