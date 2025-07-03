import asyncio
from fastapi import APIRouter, Depends
from pydantic import BaseModel
import logging

from fiber import Keypair

from api.src.socket.websocket_manager import WebSocketManager
from api.src.utils.auth import verify_request
from api.src.utils.models import TopAgentHotkey
from api.src.db.operations import DatabaseManager

db = DatabaseManager()

logger = logging.getLogger(__name__)

async def weight_receiving_agent():
    '''
    This is used to compute the current best agent. Validators can rely on this or keep a local database to compute this themselves.
    The method looks at the highest scored agents that have been considered by at least two validators. If they are within 3% of each other, it returns the oldest one
    This will be deprecated shortly in favor of validators posting weight themselves
    ''' 
    top_agent: TopAgentHotkey = await db.get_top_agent()

    return top_agent

async def tell_validators_to_set_weights():
    """Tell validators to set their weights."""
    logger.info("Starting weight setting ping")
    weights = await weight_receiving_agent()
    logger.info(f"Received weights from weight receiving agent: {weights}")
    weights_dict = weights.model_dump(mode='json')
    logger.info(f"Sending weights to all validators: {weights_dict}")
    await WebSocketManager.get_instance().send_to_all_validators("set-weights", weights_dict)
    logger.info("Sent weights to all validators")

async def run_weight_setting_loop(minutes: int):
    while True:
        await tell_validators_to_set_weights()
        await asyncio.sleep(minutes * 60)

router = APIRouter()

routes = [
    ("/weights", weight_receiving_agent, ["GET"]),
    ("/set-weights", tell_validators_to_set_weights, ["POST"])
]

for path, endpoint, methods in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["scoring"],
        dependencies=[Depends(verify_request)],
        methods=methods
    )
