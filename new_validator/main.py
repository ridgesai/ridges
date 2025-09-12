# Handles validator start and stop, including creating websocket connection, cleanup ops and version checks before anything happens, etc
from typing import Optional, Any
from enum import Enum

import websockets

import asyncio
class ValidatorStatus(Enum):
    STARTING = 'starting'
    IDLING = 'idling'
    EVALUATING = 'evaluating'
    SHUTTING_DOWN = 'shutting_down'

class RidgesValidator:
    socket_manager: Optional[Any] # TODO: typing for socket manager too
    sandbox_manager: Optional[Any] # TODO: Add typing once sandbox mgr is created
    proxy_manager: Optional[Any]
    status: ValidatorStatus

    def __init__(self) -> None:
        self.ws = None
        self.sandbox_manager = None
        self.status = ValidatorStatus.STARTING

    async def start(self):
        pass

    async def shutdown(self):
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