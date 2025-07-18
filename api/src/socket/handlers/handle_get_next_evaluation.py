from typing import Dict, Any, Optional

from api.src.backend.entities import Client, Validator
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
    if client.get_type() != "validator":
        logger.error(f"Client {client.ip_address} is not a validator. Ignoring get next evaluation request.")
        return {"status": "error", "message": "Client is not a validator"}

    from api.src.socket.websocket_manager import WebSocketManager
    ws_manager = WebSocketManager.get_instance()

    validator: 'Validator' = client

    next_evaluation = await get_next_evaluation_for_validator(validator.hotkey)
    if not next_evaluation:
        return await ws_manager.send_to_client(validator, {"event": "evaluation", "message": "No evaluations available"})
    
    miner_agent = await get_agent_by_version_id(next_evaluation.version_id)
    
    evaluation_message = {
        "event": "evaluation",
        "evaluation_id": str(next_evaluation.evaluation_id),
        "agent_version": miner_agent.model_dump(mode='json')
    }
    
    success = await ws_manager.send_to_client(validator, evaluation_message)
    return {"status": "success" if success else "error", "message": "Evaluation sent to validator" if success else "Failed to send evaluation"}
    