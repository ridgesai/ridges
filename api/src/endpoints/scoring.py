import asyncio
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List, Optional
import uuid

from api.src.backend.queries.evaluation_runs import fully_reset_evaluations, reset_validator_evaluations
from api.src.utils.config import PRUNE_THRESHOLD, SCREENING_1_THRESHOLD, SCREENING_2_THRESHOLD
from api.src.models.evaluation import Evaluation
from api.src.models.validator import Validator
from api.src.utils.auth import verify_request, verify_request_public
from loggers.logging_utils import get_logger
from api.src.backend.queries.agents import get_top_agent, ban_agents as db_ban_agents, approve_agent_version
from api.src.backend.entities import MinerAgent, MinerAgentScored
from api.src.backend.queries.agents import get_top_agent, ban_agents as db_ban_agents, approve_agent_version, get_agent_by_version_id as db_get_agent_by_version_id
from api.src.backend.entities import MinerAgentScored
from api.src.backend.db_manager import get_transaction, new_db, get_db_connection
from api.src.utils.refresh_subnet_hotkeys import check_if_hotkey_is_registered
from api.src.utils.slack import notify_unregistered_top_miner, notify_unregistered_treasury_hotkey
from api.src.backend.internal_tools import InternalTools
from api.src.backend.entities import TreasuryTransaction
from api.src.backend.queries.scores import store_treasury_transaction as db_store_treasury_transaction
from api.src.backend.queries.scores import generate_threshold_function as db_generate_threshold_function
from api.src.backend.queries.scores import evaluate_agent_for_threshold_approval
from api.src.utils.threshold_scheduler import threshold_scheduler

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
        await asyncio.sleep(minutes * 20)

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
    weights = {}  # Initialize weights dictionary

    treasury_hotkey = await get_treasury_hotkey()

    top_agent = await get_top_agent()
    weights[top_agent.miner_hotkey] = 1.0

    return weights
    # Disburse to treasury to manually send to whoever should be top agent in the event of an error
    if top_agent is None:
        weights[treasury_hotkey] = 1.0

        return weights

    weight_left = 1.0 - DUST_WEIGHT
    if top_agent.miner_hotkey.startswith("open-"):
        weights[treasury_hotkey] = weight_left
    else:
        if check_if_hotkey_is_registered(top_agent.miner_hotkey):
            weights[top_agent.miner_hotkey] = weight_left
        else:
            logger.error(f"Top agent {top_agent.miner_hotkey} not registered on subnet")
            await notify_unregistered_top_miner(top_agent.miner_hotkey)
            weights[treasury_hotkey] = 1.0

    return weights

async def get_screener_thresholds():
    """
    Returns the screener thresholds
    """
    return {"stage_1_threshold": SCREENING_1_THRESHOLD, "stage_2_threshold": SCREENING_2_THRESHOLD}

async def get_prune_threshold():
    """
    Returns the prune threshold
    """
    return {"prune_threshold": PRUNE_THRESHOLD}

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

async def approve_version(version_id: str, set_id: int, approval_password: str):
    """Approve a version ID using threshold scoring logic
    
    Args:
        version_id: The agent version to evaluate for approval
        set_id: The evaluation set ID  
        approval_password: Password for approval
    """
    if approval_password != os.getenv("APPROVAL_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid approval password. Fucker.")
    
    agent = await db_get_agent_by_version_id(version_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    try:
        # Use threshold scoring logic to determine approval action
        result = await evaluate_agent_for_threshold_approval(version_id, set_id)
        
        if result['action'] == 'approve_now':
            # Approve immediately and add to top agents history
            await approve_agent_version(version_id, set_id, None)
            
            async with get_transaction() as conn:
                await conn.execute("""
                    INSERT INTO approved_top_agents_history (version_id, set_id, top_at)
                    VALUES ($1, $2, NOW())
                """, version_id, set_id)
            
            return {
                "message": f"Agent {version_id} approved immediately - {result['reason']}",
                "action": "approve_now"
            }
            
        elif result['action'] == 'approve_future':
            # Schedule future approval
            threshold_scheduler.schedule_future_approval(
                version_id, 
                set_id, 
                result['future_approval_time']
            )
            
            # Store the future approval in approved_version_ids with future timestamp
            await approve_agent_version(version_id, set_id, result['future_approval_time'])
            
            return {
                "message": f"Agent {version_id} scheduled for approval at {result['future_approval_time'].isoformat()} - {result['reason']}",
                "action": "approve_future",
                "approval_time": result['future_approval_time'].isoformat()
            }
            
        else:  # reject
            return {
                "message": f"Agent {version_id} not approved - {result['reason']}",
                "action": "reject"
            }
            
    except Exception as e:
        logger.error(f"Error evaluating agent {version_id} for threshold approval: {e}")
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
        
        # Mark old agents as scored
        async with get_transaction() as conn:
            await conn.execute("""
                UPDATE miner_agents SET status = 'scored'
                WHERE status in ('awaiting_screening_1', 'awaiting_screening_2', 'screening_1', 'screening_2', 'waiting')
            """)
        
        # Reset approved agents to awaiting stage 1 screening
        async with get_transaction() as conn:
            # Reset approved agents to awaiting stage 1 screening
            agent_data = await conn.fetch("""
                UPDATE miner_agents SET status = 'awaiting_screening_1'
                WHERE version_id IN (SELECT version_id FROM approved_version_ids WHERE approved_at <= NOW())
                                          AND status != 'replaced'
                AND miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                RETURNING *
            """)
            
            agents_to_re_evaluate = [MinerAgent(**agent) for agent in agent_data]
            logger.info(f"Reset {len(agents_to_re_evaluate)} approved agents to awaiting_screening")
        
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

async def re_evaluate_agent(password: str, version_id: str, re_eval_screeners_and_validators: bool = False):
    """Re-evaluate an agent by resetting all validator evaluations for a version_id back to waiting status"""
    if password != os.getenv("APPROVAL_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid password")

    try:
        # Build query conditionally based on re_eval_screeners_and_validators parameter
        if re_eval_screeners_and_validators:
            # Include all evaluations (screeners and validators)
            await fully_reset_evaluations(version_id=version_id)
        else:
            await reset_validator_evaluations(version_id=version_id)
        
        return {
            "message": f"Successfully reset evaluations for version {version_id}",
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
    
async def store_treasury_transaction(dispersion_extrinsic_code: str, version_id: str, password: str, fee_extrinsic_code: Optional[str] = None):
    if password != treasury_transaction_password:
        raise HTTPException(status_code=401, detail="Invalid password. Fuck you.")

    try:
        dispersion_extrinsic_code = dispersion_extrinsic_code.strip()
        if fee_extrinsic_code:
            fee_extrinsic_code = fee_extrinsic_code.strip()

        dispersion_extrinsic_details = await internal_tools.get_transfer_stake_extrinsic_details(dispersion_extrinsic_code)
        if fee_extrinsic_code:
            fee_extrinsic_details = await internal_tools.get_transfer_stake_extrinsic_details(fee_extrinsic_code)

        if dispersion_extrinsic_details is None or (fee_extrinsic_code and fee_extrinsic_details is None):
            raise HTTPException(status_code=400, detail="Invalid extrinsic code(s)")
        
        group_transaction_id = uuid.uuid4()
        
        dispersion_transaction = TreasuryTransaction(
            group_transaction_id=group_transaction_id,
            sender_coldkey=dispersion_extrinsic_details["sender_coldkey"],
            destination_coldkey=dispersion_extrinsic_details["destination_coldkey"],
            amount_alpha=dispersion_extrinsic_details["alpha_amount"],
            fee=False,
            version_id=version_id,
            occurred_at=dispersion_extrinsic_details["occurred_at"],
            staker_hotkey=dispersion_extrinsic_details["staker_hotkey"],
            extrinsic_code=dispersion_extrinsic_code
        )

        if fee_extrinsic_code:
            fee_transaction = TreasuryTransaction(
                group_transaction_id=group_transaction_id,
                sender_coldkey=fee_extrinsic_details["sender_coldkey"],
                destination_coldkey=fee_extrinsic_details["destination_coldkey"],
                amount_alpha=fee_extrinsic_details["alpha_amount"],
                fee=True,
                version_id=version_id,
                occurred_at=fee_extrinsic_details["occurred_at"],
                staker_hotkey=fee_extrinsic_details["staker_hotkey"],
                extrinsic_code=fee_extrinsic_code
            )

        await db_store_treasury_transaction(dispersion_transaction)
        if fee_extrinsic_code:
            await db_store_treasury_transaction(fee_transaction)
        
        return {"message": "Successfully stored treasury transaction", "treasury_transactions": [dispersion_transaction.model_dump(mode='json')]}
        
    except Exception as e:
        logger.error(f"Error storing treasury transaction: {e}")
        raise HTTPException(status_code=500, detail="Error storing treasury transaction")
    
async def get_threshold_function():
    """
    Returns the threshold function with additional metadata
    """
    try:
        return await db_generate_threshold_function()
    except Exception as e:
        logger.error(f"Error generating threshold function: {e}")
        raise HTTPException(status_code=500, detail="Error generating threshold function. Please try again later.")


async def prune_agent(version_ids: str, approval_password: str):
    """Prune a specific agent by setting its status to pruned and pruning all its evaluations, a comma separated list of version_ids"""
    if approval_password != os.getenv("APPROVAL_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid approval password")
    
    try:
        for version_id in version_ids.split(","):
            async with get_transaction() as conn:
                # Check if agent exists
                agent = await conn.fetchrow("SELECT * FROM miner_agents WHERE version_id = $1", version_id)
                if not agent:
                    raise HTTPException(status_code=404, detail="Agent not found")
                
                # Update agent status to pruned
                await conn.execute("UPDATE miner_agents SET status = 'pruned' WHERE version_id = $1", version_id)
                
                # Update all evaluations for this agent to pruned status
                evaluation_count = await conn.fetchval("""
                    UPDATE evaluations 
                    SET status = 'pruned', finished_at = NOW() 
                    WHERE version_id = $1 
                    AND status IN ('waiting', 'running', 'error', 'completed')
                    AND validator_hotkey NOT LIKE 'screener-%'
                    RETURNING (SELECT COUNT(*) FROM evaluations WHERE version_id = $1)
                """, version_id)
                
                logger.info(f"Pruned agent {version_id} ({agent['agent_name']}) and {evaluation_count or 0} evaluations")
                
        return {"message": f"Successfully pruned {len(version_ids.split(','))} agents"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error pruning agent {version_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to prune agent due to internal server error")

async def check_evaluation_status(evaluation_id: str):
    """Check if an evaluation has been cancelled or is still active"""
    
    try:
        async with get_db_connection() as conn:
            # Check evaluation status
            result = await conn.fetchrow(
                "SELECT status, finished_at FROM evaluations WHERE evaluation_id = $1", 
                uuid.UUID(evaluation_id)
            )
            
            if not result:
                raise HTTPException(status_code=404, detail="Evaluation not found")
            
            status = result['status']
            finished_at = result['finished_at']
            
            # If evaluation is cancelled, replaced, pruned, or error, it should be stopped
            should_stop = status in ['cancelled', 'replaced', 'pruned', 'error']
            
            return {
                "evaluation_id": evaluation_id,
                "status": status,
                "should_stop": should_stop,
                "finished_at": finished_at.isoformat() if finished_at else None
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking evaluation status {evaluation_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to check evaluation status")

router = APIRouter()

# Public scoring endpoints (read-only data)
public_routes = [
    ("/check-top-agent", weight_receiving_agent, ["GET"]),
    ("/weights", weights, ["GET"]),
    ("/screener-thresholds", get_screener_thresholds, ["GET"]),
    ("/prune-threshold", get_prune_threshold, ["GET"]),
    ("/threshold-function", get_threshold_function, ["GET"]),
    ("/trigger-weight-update", trigger_weight_set, ["POST"]),
    ("/check-evaluation-status", check_evaluation_status, ["GET"]),
    ("/re-evaluate-agent", re_evaluate_agent, ["POST"]),
    ("/re-run-evaluation", re_run_evaluation, ["POST"]),
    ("/approve-version", approve_version, ["POST"]),
    ("/prune-agent", prune_agent, ["POST"])
]

# Protected scoring endpoints (admin functions)
protected_routes = [
    ("/ban-agents", ban_agents, ["POST"]),
    ("/re-eval-approved", re_eval_approved, ["POST"]),
    ("/refresh-scores", refresh_scores, ["POST"]),
    ("/store-treasury-transaction", store_treasury_transaction, ["POST"])
]

# Add public routes
for path, endpoint, methods in public_routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["scoring"],
        dependencies=[Depends(verify_request_public)],
        methods=methods
    )

# Add protected routes
for path, endpoint, methods in protected_routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["scoring"],
        dependencies=[Depends(verify_request)],
        methods=methods
    )
