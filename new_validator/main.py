# Handles validator start and stop, including creating websocket connection, cleanup ops and version checks before anything happens, etc
from typing import Optional, Any
from enum import Enum

from fiber import Keypair
import json

import asyncio

from new_validator.connection import ConnectionManager
from new_validator.evaluation import EvaluationManager
from new_validator.chain import ChainManager

from new_validator.utils.messaging import NewEvaluationInstruction, PlatformMessage
from validator.config import WALLET_NAME, HOTKEY_NAME

from fiber.chain.chain_utils import load_hotkey_keypair
validator_hotkey = load_hotkey_keypair(WALLET_NAME, HOTKEY_NAME)

from logging import getLogger

logger = getLogger(__name__)

class RidgesValidator:
    connection_manager: Optional['ConnectionManager']
    evaluation_manager: Optional['EvaluationManager']
    chain_manager: Optional['ChainManager']
    hotkey: Keypair

    def __init__(self, hotkey: Keypair) -> None:
        self.hotkey = hotkey
        self.start()

    async def start(self):
        '''
        The validator consists of three pieces:
            - A connection layer, that handles signing requests to platform and sending them back
            - Eval loop, that evaluates agents 
            - Weight loop, that periodically sets weights on chain
        '''

        # Create connection, eval managers
        self.connection_manager = ConnectionManager(hotkey=self.hotkey, pass_message_to_validator=self.handle_platform_instructions)
        self.evaluation_manager = EvaluationManager(connection_manager=self.connection_manager)
        self.chain_manager = ChainManager(hotkey=self.hotkey)

        return
    
    async def handle_platform_instructions(self, message: str):
        """Process incoming platform message and route to validator
        
        Parameters
        ----------
        message: raw string from websocket
        """
        try:
            parsed_message = json.loads(message)
            event = parsed_message.get("event", None)

            if event is None:
                raise Exception(f"Platform sent message without a defined event: {message}")
            
            match event:
                case "evaluation":
                    self.evaluation_manager.run_evaluation(evaluation=NewEvaluationInstruction(**parsed_message))

                case "set-weights":
                    self.chain_manager.set_weight()

                case _: 
                    logger.info(f"Validator received unrecognized message: {message}")
        except Exception as e:
            logger.warning(f"Error parsing message from websocket: {e}")
        
    async def shutdown(self):
        # Kill connections and gracefully report shutdown
        self.connection_manager.close_connections()
        # And then kill running evaluations. Run a cleanup on containers
        self.evaluation_manager.shutdown_evaluations()
        
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