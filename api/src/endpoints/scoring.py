import asyncio
import os
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException

from api.src.socket.websocket_manager import WebSocketManager
from api.src.utils.auth import verify_request
from api.src.utils.models import TopAgentHotkey
from api.src.db.operations import DatabaseManager
from api.src.utils.logging_utils import get_logger

load_dotenv()

db = DatabaseManager()

logger = get_logger(__name__)

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

## Actual endpoints ##

async def weight_receiving_agent():
    '''
    This is used to compute the current best agent. Validators can rely on this or keep a local database to compute this themselves.
    The method looks at the highest scored agents that have been considered by at least two validators. If they are within 3% of each other, it returns the oldest one
    This will be deprecated shortly in favor of validators posting weight themselves
    ''' 
    top_agent: TopAgentHotkey = await db.get_top_agent()

    return top_agent

async def ban_agent(agent_id: str, ban_password: str):
    if ban_password != os.getenv("BAN_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid ban password. Fuck you.")

    try:
        result = await db.ban_agent(agent_id)
    except Exception as e:
        logger.error(f"Error banning agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to ban agent due to internal server error. Please try again later.")
    
    if result == 0:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    return {"message": "Agent banned successfully"}

async def get_pending_approvals(status: str = "pending", limit: int = 50):
    """Get pending approvals queue"""
    try:
        pending_approvals = await db.get_pending_approvals(status=status, limit=limit)
        return {
            "pending_approvals": pending_approvals,
            "count": len(pending_approvals)
        }
    except Exception as e:
        logger.error(f"Error getting pending approvals: {e}")
        raise HTTPException(status_code=500, detail="Failed to get pending approvals due to internal server error.")

async def trigger_weight_set():
    tell_validators_to_set_weights()
    return {"message": "Successfully triggered weight update"}

async def approve_version(version_id: str, approval_password: str):
    """Approve a version ID for weight consideration"""
    if approval_password != os.getenv("APPROVAL_PASSWORD"):
        raise HTTPException(status_code=401, detail="Invalid approval password. fucker")

    try:
        result = await db.approve_version_id(version_id)
    except Exception as e:
        logger.error(f"Error approving version {version_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to approve version due to internal server error. Please try again later.")
    
    if result == 0:
        raise HTTPException(status_code=404, detail="Version not found")
    
    # Update pending approval status to 'approved' if it exists
    try:
        await db.update_pending_approval_status(version_id, "approved")
        logger.info(f"Updated pending approval status to 'approved' for version {version_id}")
    except Exception as e:
        logger.warning(f"Could not update pending approval status for version {version_id}: {e}")
        # Don't fail the request - the approval was successful
    
    # Try to update the approved leader if this version has a better score
    try:
        leader_updated = await db.update_approved_leader_if_better(version_id)
        if leader_updated:
            logger.info(f"Version {version_id} approved and set as new approved leader")
            return {"message": "Version approved successfully and set as new approved leader"}
        else:
            logger.info(f"Version {version_id} approved but not set as leader (score not high enough)")
            return {"message": "Version approved successfully"}
    except Exception as e:
        logger.error(f"Error updating approved leader for version {version_id}: {e}")
        # Don't fail the whole request - the approval was successful
        return {"message": "Version approved successfully (leader update failed)"}
    
router = APIRouter()

routes = [
    ("/weights", weight_receiving_agent, ["GET"]),
    ("/ban-agent", ban_agent, ["POST"]),
    ("/approve-version", approve_version, ["POST"]),
    ("/trigger-weight-update", trigger_weight_set, ["POST"]),
    ("/pending-approvals", get_pending_approvals, ["GET"])
]

for path, endpoint, methods in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["scoring"],
        dependencies=[Depends(verify_request)],
        methods=methods
    )
