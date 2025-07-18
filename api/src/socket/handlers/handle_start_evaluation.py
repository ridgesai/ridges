from typing import Dict, Any

from api.src.backend.entities import Client
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def handle_start_evaluation(
    client: Client,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle start-evaluation message from a client"""
    # Validate client type
    if client.get_type() not in ["validator", "screener"]:
        logger.error(f"Client {client.ip_address} is not a validator or screener. Ignoring evaluation start.")
        return {"status": "error", "message": "Client is not a validator or screener"}
    
    from api.src.backend.agent_machine import AgentStateMachine
    state_machine = AgentStateMachine.get_instance()
    evaluation_id = response_json["evaluation_id"]
    
    # Use appropriate start method based on client type
    if client.get_type() == "screener":
        success = await state_machine.start_screening(evaluation_id, client.hotkey)
        action = "Screening"
    else:
        success = await state_machine.start_evaluation(evaluation_id, client.hotkey)
        action = "Evaluation"
    
    status = "success" if success else "error"
    message = f"{action} {'started' if success else 'failed to start'}"
    
    return {"status": status, "message": message}
