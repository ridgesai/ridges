import os
from typing import List, Dict

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException

import utils.logger as logger
from api import config
from api.queries.scores import get_weight_receiving_agent_hotkey, get_treasury_hotkey
from api.src.backend.queries.agents import get_top_agent, ban_agents as db_ban_agents
from api.src.backend.queries.evaluation_runs import fully_reset_evaluations, reset_validator_evaluations
from api.src.backend.queries.scores import generate_threshold_function as db_generate_threshold_function
from api.src.utils.auth import verify_request, verify_request_public
from api.src.utils.refresh_subnet_hotkeys import check_if_hotkey_is_registered

load_dotenv()

router = APIRouter()

treasury_transaction_password = os.getenv("TREASURY_TRANSACTION_PASSWORD")



## Actual endpoints ##

@router.get("/check-top-agent", tags=["scoring"], dependencies=[Depends(verify_request_public)])
async def weight_receiving_agent():
    '''
    This is used to compute the current best agent. Validators can rely on this or keep a local database to compute this themselves.
    The method looks at the highest scored agents that have been considered by at least two validators. If they are within 3% of each other, it returns the oldest one
    This will be deprecated shortly in favor of validators posting weight themselves
    ''' 
    top_agent = await get_top_agent()

    return top_agent

async def get_treasury_hotkey_if_exists():
    """
    Returns the most recently created active treasury hotkey.
    Later, return the wallet with the least funs to mitigate risk of large wallets
    """
    treasury_hotkey = await get_treasury_hotkey()
    if not treasury_hotkey:
        raise ValueError("No active treasury wallets found in database")

    if not check_if_hotkey_is_registered(treasury_hotkey):
        logger.error(f"Treasury hotkey {treasury_hotkey} not registered on subnet")

    return treasury_hotkey


@router.get("/weights")
async def weights() -> Dict[str, float]:
    """
    Returns a dictionary of miner hotkeys to weights
    """
    DUST_WEIGHT = 1/65535 # 1/(2^16 - 1), smallest weight possible
    weights = {}  # Initialize weights dictionary

    treasury_hotkey = await get_treasury_hotkey_if_exists()

    top_agent_hotkey = await get_weight_receiving_agent_hotkey()

    # Disburse to treasury to manually send to whoever should be top agent in the event of an error
    if top_agent_hotkey is None:
        weights[treasury_hotkey] = 1.0

        return weights

    weight_left = 1.0 - DUST_WEIGHT
    if check_if_hotkey_is_registered(top_agent_hotkey):
        weights[top_agent_hotkey] = weight_left
    else:
        logger.error(f"Top agent {top_agent_hotkey} not registered on subnet")
        weights[treasury_hotkey] = 1.0

    return weights


@router.get("/screener-thresholds", tags=["scoring"], dependencies=[Depends(verify_request_public)])
async def get_screener_thresholds():
    """
    Returns the screener thresholds
    """
    return {"stage_1_threshold": config.SCREENER_1_THRESHOLD, "stage_2_threshold": config.SCREENER_2_THRESHOLD}

@router.get("/prune-threshold", tags=["scoring"], dependencies=[Depends(verify_request_public)])
async def get_prune_threshold():
    """
    Returns the prune threshold
    """
    return {"prune_threshold": config.PRUNE_THRESHOLD}


@router.post("/ban-agents", tags=["scoring"], dependencies=[Depends(verify_request)])
async def ban_agents(agent_ids: List[str], reason: str, ban_password: str):
    if ban_password != os.getenv("BAN_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid ban password. Fuck you.")

    try:
        await db_ban_agents(agent_ids, reason)
        return {"message": "Agents banned successfully"}
    except Exception as e:
        logger.error(f"Error banning agents: {e}")
        raise HTTPException(status_code=500, detail="Failed to ban agent due to internal server error. Please try again later.")


# @router.post("/approve-version", tags=["scoring"], dependencies=[Depends(verify_request_public)])
# async def approve_version(agent_id: str, set_id: int, approval_password: str):
#     """Approve a version ID using threshold scoring logic
#
#     Args:
#         agent_id: The agent version to evaluate for approval
#         set_id: The evaluation set ID
#         approval_password: Password for approval
#     """
#     if approval_password != os.getenv("APPROVAL_PASSWORD"):
#         raise HTTPException(status_code=401, detail="Invalid approval password. Fucker.")
#
#     agent = await db_get_agent_by_agent_id(agent_id)
#     if not agent:
#         raise HTTPException(status_code=404, detail="Agent not found")
#
#     try:
#         # Use threshold scoring logic to determine approval action
#         result = await evaluate_agent_for_threshold_approval(agent_id, set_id)
#
#         if result['action'] == 'approve_now':
#             # Approve immediately and add to top agents history
#             await approve_agent_version(agent_id, set_id, None)
#
#             async with get_transaction() as conn:
#                 await conn.execute("""
#                     INSERT INTO approved_top_agents_history (agent_id, set_id, top_at)
#                     VALUES ($1, $2, NOW())
#                 """, agent_id, set_id)
#
#             return {
#                 "message": f"Agent {agent_id} approved immediately - {result['reason']}",
#                 "action": "approve_now"
#             }
#
#         elif result['action'] == 'approve_future':
#             # Schedule future approval
#             threshold_scheduler.schedule_future_approval(
#                 agent_id,
#                 set_id,
#                 result['future_approval_time']
#             )
#
#             # Store the future approval in approved_agent_ids with future timestamp
#             await approve_agent_version(agent_id, set_id, result['future_approval_time'])
#
#             return {
#                 "message": f"Agent {agent_id} scheduled for approval at {result['future_approval_time'].isoformat()} - {result['reason']}",
#                 "action": "approve_future",
#                 "approval_time": result['future_approval_time'].isoformat()
#             }
#
#         else:  # reject
#             return {
#                 "message": f"Agent {agent_id} not approved - {result['reason']}",
#                 "action": "reject"
#             }
#
#     except Exception as e:
#         logger.error(f"Error evaluating agent {agent_id} for threshold approval: {e}")
#         raise HTTPException(status_code=500, detail="Failed to approve version due to internal server error. Please try again later.")


@router.post("/re-evaluate-agent", tags=["scoring"], dependencies=[Depends(verify_request_public)])
async def re_evaluate_agent(password: str, agent_id: str, re_eval_screeners_and_validators: bool = False):
    """Re-evaluate an agent by resetting all validator evaluations for a agent_id back to waiting status"""
    if password != os.getenv("APPROVAL_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid password")

    try:
        # Build query conditionally based on re_eval_screeners_and_validators parameter
        if re_eval_screeners_and_validators:
            # Include all evaluations (screeners and validators)
            await fully_reset_evaluations(agent_id=agent_id)
        else:
            await reset_validator_evaluations(agent_id=agent_id)
        
        return {
            "message": f"Successfully reset evaluations for version {agent_id}",
        }
            
    except Exception as e:
        logger.error(f"Error resetting validator evaluations for version {agent_id}: {e}")
        raise HTTPException(status_code=500, detail="Error resetting validator evaluations")

# @router.post("/re-run-evaluation", tags=["scoring"], dependencies=[Depends(verify_request_public)])
# async def re_run_evaluation(password: str, evaluation_id: str):
#     """Re-run an evaluation by resetting it to waiting status"""
#     if password != os.getenv("APPROVAL_PASSWORD"):
#         raise HTTPException(status_code=401, detail="Invalid password")
#
#     try:
#         async with get_transaction() as conn:
#             evaluation = await Evaluation.get_by_id(evaluation_id)
#             await evaluation.reset_to_waiting(conn)
#             return {"message": f"Successfully reset evaluation {evaluation_id}"}
#     except Exception as e:
#         logger.error(f"Error resetting evaluation {evaluation_id}: {e}")
#         raise HTTPException(status_code=500, detail="Error resetting evaluation")
    
# @router.post("/store-treasury-transaction", tags=["scoring"], dependencies=[Depends(verify_request)])
# async def store_treasury_transaction(dispersion_extrinsic_code: str, agent_id: str, password: str, fee_extrinsic_code: Optional[str] = None):
#     if password != treasury_transaction_password:
#         raise HTTPException(status_code=401, detail="Invalid password. Fuck you.")
#
#     try:
#         dispersion_extrinsic_code = dispersion_extrinsic_code.strip()
#         if fee_extrinsic_code:
#             fee_extrinsic_code = fee_extrinsic_code.strip()
#
#         dispersion_extrinsic_details = await internal_tools.get_transfer_stake_extrinsic_details(dispersion_extrinsic_code)
#         if fee_extrinsic_code:
#             fee_extrinsic_details = await internal_tools.get_transfer_stake_extrinsic_details(fee_extrinsic_code)
#
#         if dispersion_extrinsic_details is None or (fee_extrinsic_code and fee_extrinsic_details is None):
#             raise HTTPException(status_code=400, detail="Invalid extrinsic code(s)")
#
#         group_transaction_id = uuid.uuid4()
#
#         dispersion_transaction = TreasuryTransaction(
#             group_transaction_id=group_transaction_id,
#             sender_coldkey=dispersion_extrinsic_details["sender_coldkey"],
#             destination_coldkey=dispersion_extrinsic_details["destination_coldkey"],
#             amount_alpha=dispersion_extrinsic_details["alpha_amount"],
#             fee=False,
#             agent_id=agent_id,
#             occurred_at=dispersion_extrinsic_details["occurred_at"],
#             staker_hotkey=dispersion_extrinsic_details["staker_hotkey"],
#             extrinsic_code=dispersion_extrinsic_code
#         )
#
#         if fee_extrinsic_code:
#             fee_transaction = TreasuryTransaction(
#                 group_transaction_id=group_transaction_id,
#                 sender_coldkey=fee_extrinsic_details["sender_coldkey"],
#                 destination_coldkey=fee_extrinsic_details["destination_coldkey"],
#                 amount_alpha=fee_extrinsic_details["alpha_amount"],
#                 fee=True,
#                 agent_id=agent_id,
#                 occurred_at=fee_extrinsic_details["occurred_at"],
#                 staker_hotkey=fee_extrinsic_details["staker_hotkey"],
#                 extrinsic_code=fee_extrinsic_code
#             )
#
#         await db_store_treasury_transaction(dispersion_transaction)
#         if fee_extrinsic_code:
#             await db_store_treasury_transaction(fee_transaction)
#
#         return {"message": "Successfully stored treasury transaction", "treasury_transactions": [dispersion_transaction.model_dump(mode='json')]}
#
#     except Exception as e:
#         logger.error(f"Error storing treasury transaction: {e}")
#         raise HTTPException(status_code=500, detail="Error storing treasury transaction")
    
@router.get("/threshold-function", tags=["scoring"], dependencies=[Depends(verify_request_public)])
async def get_threshold_function():
    """
    Returns the threshold function with additional metadata
    """
    try:
        return await db_generate_threshold_function()
    except Exception as e:
        logger.error(f"Error generating threshold function: {e}")
        raise HTTPException(status_code=500, detail="Error generating threshold function. Please try again later.")
