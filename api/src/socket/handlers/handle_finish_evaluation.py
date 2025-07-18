
from typing import Dict, Any, Optional

from api.src.backend.entities import Client
from api.src.backend.queries.evaluations import get_evaluation_by_evaluation_id
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def handle_finish_evaluation(
    client: Client,
    response_json: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Handle finish-evaluation message from a client"""
    # Validate client type
    if client.get_type() not in ["validator", "screener"]:
        logger.error(f"Client {client.ip_address} is not a validator or screener. Ignoring finish evaluation request.")
        return {"status": "error", "message": "Client is not a validator or screener"}
    
    from api.src.backend.agent_machine import AgentStateMachine
    state_machine = AgentStateMachine.get_instance()
    
    evaluation_id = response_json["evaluation_id"]
    errored = response_json.get("errored", False)
    
    
    # Get the evaluation to check if it's a screener evaluation
    evaluation = await get_evaluation_by_evaluation_id(evaluation_id)
    if not evaluation:
        logger.warning(f"Evaluation {evaluation_id} not found")
        return {"status": "error", "message": "Evaluation not found"}
    
    is_screener_evaluation = evaluation.validator_hotkey.startswith("i-0")
    is_screener_client = client.get_type() == "screener"
    
    # Validate client type matches evaluation type
    if is_screener_client and not is_screener_evaluation:
        logger.error(f"Screener {client.hotkey} trying to finish validator evaluation {evaluation_id}")
        return {"status": "error", "message": "Screeners cannot finish validator evaluations"}
    elif not is_screener_client and is_screener_evaluation:
        logger.error(f"Validator {client.hotkey} trying to finish screener evaluation {evaluation_id}")
        return {"status": "error", "message": "Validators cannot finish screener evaluations"}
    
    try:
        logger.info(f"{client.get_type().title()} {client.hotkey} has finished evaluation {evaluation_id}.")
        
        # Use appropriate finish method based on evaluation type
        if is_screener_evaluation:
            success = await state_machine.finish_screening(client, evaluation_id)
            action = "Screening"
        else:
            success = await state_machine.finish_evaluation(client, evaluation_id, errored)
            action = "Evaluation"
        
        if success:
            logger.info(f"{action} {evaluation_id} finished successfully by {client.get_type()} {client.hotkey}")
            
            # Check if we should assign more work to this client
            if client.get_type() == "screener":
                await state_machine.screener_connect(client)
            
            return {"status": "success", "message": f"{action} finished successfully"}
        else:
            logger.warning(f"Failed to finish {action.lower()} {evaluation_id} for {client.get_type()} {client.hotkey}")
            return {"status": "error", "message": f"Failed to finish {action.lower()}"}
            
    except Exception as e:
        logger.error(f"Error finishing evaluation for {client.get_type()} {client.hotkey}: {str(e)}")
        return {"status": "error", "message": f"Failed to finish evaluation: {str(e)}"} 