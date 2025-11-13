import os
from typing import Dict

from dotenv import load_dotenv
from fastapi import APIRouter

import utils.logger as logger
from queries.scores import get_weight_receiving_agent_hotkey
from api.src.utils.bittensor import check_if_hotkey_is_registered

load_dotenv()

router = APIRouter()

treasury_transaction_password = os.getenv("TREASURY_TRANSACTION_PASSWORD")

# NOTE: Used in validator/main.py
@router.get("/weights")
async def weights() -> Dict[str, float]:
    """
    Returns a dictionary of miner hotkeys to weights
    If no top agent, don't set weights
    """
    weights: Dict[str, float] = {}

    # For cases where there is no top miner, or the top miner is not registered on the subnet
    OWNER_HOTKEY = "5EsNzkZ3DwDqCsYmSJDeGXX51dQJd5broUCH6dbDjvkTcicD"



    # # If we need to manually burn, set this to True.
    # BURN = True
    # if BURN:
    #     weights[OWNER_HOTKEY] = 1.0
    #     return weights



    top_agent_hotkey = await get_weight_receiving_agent_hotkey()

    

    if top_agent_hotkey is not None:
        if await check_if_hotkey_is_registered(top_agent_hotkey):
            weights[top_agent_hotkey] = 1.0
        else:
            logger.error(f"Top agent {top_agent_hotkey} not registered on subnet. Setting weight to owner hotkey ({OWNER_HOTKEY})")
            weights[OWNER_HOTKEY] = 1.0
    else:
        logger.info(f"No top agent found. Setting weight to owner hotkey ({OWNER_HOTKEY})")
        weights[OWNER_HOTKEY] = 1.0

    return weights