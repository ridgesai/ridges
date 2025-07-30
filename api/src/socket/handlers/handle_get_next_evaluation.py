from typing import Dict, Any, Optional

from api.src.backend.entities import Client
from api.src.models.validator import Validator
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
    
    validator: 'Validator' = client
    
    # Use the same atomic lock pattern as other validator operations
    from api.src.models.evaluation import Evaluation
    async with Evaluation.get_lock():
        if not validator.is_available():
            logger.info(f"Validator {validator.hotkey} not available for get-next-evaluation")
            return {"status": "error", "message": "Validator not available"}
        
        evaluation_id = await validator.get_next_evaluation()
        if not evaluation_id:
            await validator.websocket.send_json({"event": "evaluation", "message": "No evaluations available"})
            return {"status": "error", "message": "No evaluations available"}
        
        success = await validator.start_evaluation_and_send(evaluation_id)
        
        return {"status": "success" if success else "error", "message": "Evaluation sent to validator" if success else "Failed to send evaluation"}
    