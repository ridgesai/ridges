from typing import Dict, Any, Optional

from api.src.backend.entities import Client
from api.src.models.validator import Validator
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
    from api.src.backend.queries.agents import get_agent_by_version_id
    ws_manager = WebSocketManager.get_instance()

    validator: 'Validator' = client

    evaluation_id = await validator.get_next_evaluation()
    if not evaluation_id:
        return await ws_manager.send_to_client(validator, {"event": "evaluation", "message": "No evaluations available"})
    
    # Get evaluation details
    from api.src.models.evaluation import Evaluation
    evaluation = await Evaluation.get_by_id(evaluation_id)
    if not evaluation:
        return {"status": "error", "message": "Evaluation not found"}
    
    miner_agent = await get_agent_by_version_id(evaluation.version_id)
    
    evaluation_message = {
        "event": "evaluation",
        "evaluation_id": evaluation_id,
        "agent_version": miner_agent.model_dump(mode='json')
    }
    
    success = await ws_manager.send_to_client(validator, evaluation_message)
    return {"status": "success" if success else "error", "message": "Evaluation sent to validator" if success else "Failed to send evaluation"}
    