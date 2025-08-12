import asyncio
import os
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List

from api.src.models.evaluation import Evaluation
from api.src.models.validator import Validator
from api.src.utils.auth import verify_request
from api.src.utils.models import TopAgentHotkey
from loggers.logging_utils import get_logger
from api.src.backend.queries.agents import get_top_agent, ban_agents as db_ban_agents, approve_agent_version
from api.src.backend.entities import MinerAgentScored
from api.src.backend.db_manager import get_transaction, new_db, get_db_connection
from api.src.utils.refresh_subnet_hotkeys import check_if_hotkey_is_registered
from api.src.utils.slack import notify_unregistered_top_miner, notify_unregistered_treasury_hotkey
from api.src.backend.internal_tools import InternalTools
from api.src.backend.entities import TreasuryTransaction
from api.src.backend.queries.scores import store_treasury_transaction as db_store_treasury_transaction

load_dotenv()

logger = get_logger(__name__)
treasury_transaction_password = os.getenv("TREASURY_TRANSACTION_PASSWORD")

internal_tools = InternalTools()

async def tell_validators_to_set_weights():
    """Tell validators to set their weights."""

    for validator in await Validator.get_connected():
        await validator.websocket.send_json({"event": "set-weights"})

    logger.info(f"Sent weight setting event to all validators")

async def run_weight_setting_loop(minutes: int):
    while True:
        await tell_validators_to_set_weights()
        await asyncio.sleep(minutes * 60)

## Actual endpoints ##

async def weight_receiving_agent():
    '''
    This is used to compute the current best agent. Validators can rely on this or keep a local database to compute this themselves.
    The method looks at the highest scored agents that have been considered by at least two validators. If they are within 3% of each other, it returns the oldest one
    This will be deprecated shortly in favor of validators posting weight themselves
    ''' 
    top_agent = await get_top_agent()

    return top_agent

async def get_treasury_hotkey():
    """
    Returns the most recently created active treasury hotkey.
    Later, return the wallet with the least funs to mitigate risk of large wallets
    """
    async with get_db_connection() as conn:
        treasury_hotkey_data = await conn.fetch("""
            SELECT hotkey FROM treasury_wallets WHERE active = TRUE ORDER BY created_at DESC LIMIT 1
        """)
        if not treasury_hotkey_data:
            raise ValueError("No active treasury wallets found in database")
        treasury_hotkey = treasury_hotkey_data[0]["hotkey"]

        if not check_if_hotkey_is_registered(treasury_hotkey):
            logger.error(f"Treasury hotkey {treasury_hotkey} not registered on subnet")
            await notify_unregistered_treasury_hotkey(treasury_hotkey)
        
        return treasury_hotkey

async def weights() -> Dict[str, float]:
    """
    Returns a dictionary of miner hotkeys to weights
    """
    DUST_WEIGHT = 1/65535 # 1/(2^16 - 1), smallest weight possible

    async with get_db_connection() as conn:
        approved_agent_hotkeys_data = await conn.fetch("""
            SELECT DISTINCT miner_hotkey FROM approved_version_ids
            LEFT JOIN miner_agents ma on ma.version_id = approved_version_ids.version_id
            WHERE ma.miner_hotkey NOT LIKE 'open-%'
        """)
        approved_agent_hotkeys = [row["miner_hotkey"] for row in approved_agent_hotkeys_data]

    weights = {hotkey: DUST_WEIGHT for hotkey in approved_agent_hotkeys} 

    top_agent = await get_top_agent()

    if top_agent is None:
        return weights

    weight_left = 1.0 - DUST_WEIGHT * len(approved_agent_hotkeys)
    if top_agent.miner_hotkey.startswith("open-"):
        weights[await get_treasury_hotkey()] = weight_left
    else:
        if check_if_hotkey_is_registered(top_agent.miner_hotkey):
            weights[top_agent.miner_hotkey] = weight_left
        else:
            logger.error(f"Top agent {top_agent.miner_hotkey} not registered on subnet")
            await notify_unregistered_top_miner(top_agent.miner_hotkey)
            weights[await get_treasury_hotkey()] = weight_left

    return weights

async def ban_agents(agent_ids: List[str], reason: str, ban_password: str):
    if ban_password != os.getenv("BAN_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid ban password. Fuck you.")

    try:
        await db_ban_agents(agent_ids, reason)
        return {"message": "Agents banned successfully"}
    except Exception as e:
        logger.error(f"Error banning agents: {e}")
        raise HTTPException(status_code=500, detail="Failed to ban agent due to internal server error. Please try again later.")
    

async def trigger_weight_set():
    await tell_validators_to_set_weights()
    return {"message": "Successfully triggered weight update"}

async def approve_version(version_id: str, approval_password: str):
    """Approve a version ID for weight consideration"""
    if approval_password != os.getenv("APPROVAL_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid approval password. fucker")

    try:
        await approve_agent_version(version_id)
        return {"message": f"Successfully approved {version_id}"}
    except Exception as e:
        logger.error(f"Error approving version {version_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to approve version due to internal server error. Please try again later.")

async def re_eval_approved(approval_password: str):
    """
    Re-evaluate approved agents with the newest evaluation set
    by setting the miner_agents status to "awaiting_screening"
    """
    if approval_password != os.getenv("APPROVAL_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid approval password")
    
    try:
        logger.info("Starting re-evaluation of approved agents")
        
        # Use screener to handle the entire re-evaluation flow
        from api.src.models.screener import Screener
        
        agents_to_re_evaluate = await Screener.re_evaluate_approved_agents()
        
        if not agents_to_re_evaluate:
            logger.info("No approved agents found for re-evaluation")
            return {"message": "No approved agents found for re-evaluation", "agents": []}
        
        logger.info(f"Successfully initiated re-evaluation for {len(agents_to_re_evaluate)} approved agents")
        return {
            "message": f"Successfully initiated re-evaluation for {len(agents_to_re_evaluate)} approved agents",
            "agents": [agent.model_dump(mode='json') for agent in agents_to_re_evaluate]
        }

    except Exception as e:
        logger.error(f"Error re-evaluating approved agents: {e}")
        raise HTTPException(status_code=500, detail="Error initiating re-evaluation of approved agents")

async def refresh_scores():
    """Manually refresh the agent_scores materialized view"""
    try:
        async with new_db.acquire() as conn:
            await MinerAgentScored.refresh_materialized_view(conn)
        logger.info("Successfully refreshed agent_scores materialized view")
        return {"message": "Successfully refreshed agent scores"}
    except Exception as e:
        logger.error(f"Error refreshing agent scores: {e}")
        raise HTTPException(status_code=500, detail="Error refreshing agent scores")

async def re_evaluate_agent(password: str, version_id: str):
    """Re-evaluate an agent by resetting all validator evaluations for a version_id back to waiting status"""
    if password != os.getenv("APPROVAL_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid password")

    try:
        async with get_transaction() as conn:
            validator_evaluations = await conn.fetch("""
                SELECT * FROM evaluations WHERE version_id = $1
                    AND validator_hotkey NOT LIKE 'screener-%'
                    AND validator_hotkey NOT LIKE 'i-0%'
            """, version_id)
            
            for evaluation_data in validator_evaluations:
                evaluation = Evaluation(**evaluation_data)
                await evaluation.reset_to_waiting(conn)
            
            logger.info(f"Reset {len(validator_evaluations)} validator evaluations for version {version_id}")
            return {
                "message": f"Successfully reset {len(validator_evaluations)} validator evaluations for version {version_id}",
            }
            
    except Exception as e:
        logger.error(f"Error resetting validator evaluations for version {version_id}: {e}")
        raise HTTPException(status_code=500, detail="Error resetting validator evaluations")

async def re_run_evaluation(password: str, evaluation_id: str):
    """Re-run an evaluation by resetting it to waiting status"""
    if password != os.getenv("APPROVAL_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid password")
    
    try:
        async with get_transaction() as conn:
            evaluation = await Evaluation.get_by_id(evaluation_id)
            await evaluation.reset_to_waiting(conn)
            return {"message": f"Successfully reset evaluation {evaluation_id}"}
    except Exception as e:
        logger.error(f"Error resetting evaluation {evaluation_id}: {e}")
        raise HTTPException(status_code=500, detail="Error resetting evaluation")
    
async def store_treasury_transaction(extrinsic_link: str, fee_alpha: int, version_id: str, password: str):
    if password != treasury_transaction_password:
        raise HTTPException(status_code=401, detail="Invalid password. Fuck you.")

    try:
        extrinsic_code = extrinsic_link.strip().rsplit('/', 1)[-1].strip()
        print(extrinsic_code)
        extrinsic_details = await internal_tools.get_transfer_stake_extrinsic_details(extrinsic_code)
        if extrinsic_details is None:
            raise HTTPException(status_code=400, detail="Invalid extrinsic link")
        
        print(extrinsic_details)
        
        treasury_transaction = TreasuryTransaction(
            sender_coldkey=extrinsic_details["sender_coldkey"],
            destination_coldkey=extrinsic_details["destination_coldkey"],
            amount_alpha=extrinsic_details["alpha_amount"],
            fee_alpha=fee_alpha,
            version_id=version_id,
            occured_at=extrinsic_details["occured_at"],
            staker_hotkey=extrinsic_details["staker_hotkey"]
        )

        print(treasury_transaction)

        await db_store_treasury_transaction(treasury_transaction)
        
        return {"message": "Successfully stored treasury transaction", "treasury_transaction": treasury_transaction.model_dump(mode='json')}
        
    except Exception as e:
        logger.error(f"Error storing treasury transaction: {e}")
        raise HTTPException(status_code=500, detail="Error storing treasury transaction")

router = APIRouter()

routes = [
    ("/check-top-agent", weight_receiving_agent, ["GET"]),
    ("/weights", weights, ["GET"]),
    ("/ban-agents", ban_agents, ["POST"]),
    ("/approve-version", approve_version, ["POST"]),
    ("/trigger-weight-update", trigger_weight_set, ["POST"]),
    ("/re-eval-approved", re_eval_approved, ["POST"]),
    ("/refresh-scores", refresh_scores, ["POST"]),
    ("/re-evaluate-agent", re_evaluate_agent, ["POST"]),
    ("/re-run-evaluation", re_run_evaluation, ["POST"]),
    ("/store-treasury-transaction", store_treasury_transaction, ["POST"])
]

for path, endpoint, methods in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["scoring"],
        dependencies=[Depends(verify_request)],
        methods=methods
    )
