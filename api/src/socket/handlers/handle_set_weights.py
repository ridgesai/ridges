from typing import Dict, Any

from api.src.backend.queries.agents import get_top_agent
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def handle_set_weights_after_evaluation() -> Dict[str, Any]:
    """Handle set-weights logic after finishing an evaluation"""
    
    try:
        top_agent = await get_top_agent()  # returns TopAgentHotkey

        if top_agent and top_agent.miner_hotkey:
            weights_data = {
                "miner_hotkey": top_agent.miner_hotkey,
                "version_id": str(top_agent.version_id),
                    "avg_score": top_agent.avg_score,
            }
            logger.info(f"Platform socket broadcasting set-weights for hotkey {top_agent.miner_hotkey} to validators")
            return weights_data
        else:
            logger.warning("Could not determine top miner – skipping set-weights broadcast")
            return {"error": "Could not determine top miner"}
            
    except Exception as e:
        logger.error(f"Failed to broadcast set-weights: {e}")
        return {"error": f"Failed to broadcast set-weights: {str(e)}"} 