from typing import Dict, Any, Optional

from api.src.backend.entities import Client
from api.src.backend.queries.evaluations import get_next_evaluation_for_validator
from api.src.backend.queries.agents import get_agent_by_version_id
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def handle_get_next_evaluation(
    client: Client,
    response_json: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Handle get-next-evaluation message from a client"""
    # Validate client type
    if client.get_type() not in ["validator", "screener"]:
        logger.error(f"Client {client.ip_address} is not a validator or screener. Ignoring get next evaluation request.")
        return {"status": "error", "message": "Client is not a validator or screener"}
    
    evaluation_id = response_json.get("evaluation_id")
    
    if client.get_type() == "screener":
        # For screeners, we handle connecting and getting work
        from api.src.backend.state_machine import EvaluationStateMachine
        state_machine = EvaluationStateMachine.get_instance()
        success = await state_machine.handle_screener_connect(client)
        return {"status": "success" if success else "error", "message": "Screener work assigned" if success else "No work available"}
    else:
        # For validators, get next available evaluation
        next_evaluation = await get_next_evaluation_for_validator(client.hotkey)
        
        if not next_evaluation:
            return {"status": "no_evaluation", "message": "No evaluations available"}
        
        # Get the associated agent information
        miner_agent = await get_agent_by_version_id(next_evaluation.version_id)
        
        return {
            "status": "success",
            "message": "Next evaluation retrieved",
            "evaluation": {
                "evaluation_id": str(next_evaluation.evaluation_id),
                "agent_version": miner_agent.model_dump(mode='json')
            }
        } 