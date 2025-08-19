from typing import Dict, Any

from api.src.backend.entities import Client, EvaluationRun
from api.src.backend.queries.evaluation_runs import store_evaluation_run
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def handle_upsert_evaluation_run(
    client: Client,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle upsert-evaluation-run message from a client"""
    # Validate client type
    if client.get_type() not in ["validator", "screener"]:
        logger.error(f"Client {client.ip_address} is not a validator or screener. Ignoring upsert evaluation run request.")
        return {"status": "error", "message": "Client is not a validator or screener"}
    
    evaluation_run_data = response_json.get("evaluation_run")
    
    if not evaluation_run_data:
        return {"status": "error", "message": "Missing evaluation_run data"}
    
    try:
        logger.info(f"{client.get_type().title()} {client.hotkey} sent an evaluation run. Upserting evaluation run.")
        
        # Convert to EvaluationRun object and store
        evaluation_run = EvaluationRun(**evaluation_run_data)
        await store_evaluation_run(evaluation_run)
        
        # Broadcast update to connected clients
        from api.src.socket.websocket_manager import WebSocketManager
        ws = WebSocketManager.get_instance()
        
        # Prepare broadcast data
        broadcast_data = evaluation_run.model_dump(mode='json')
        # Ensure UUIDs are converted to strings
        if 'run_id' in broadcast_data and isinstance(broadcast_data['run_id'], str) == False:
            broadcast_data['run_id'] = str(broadcast_data['run_id'])
        if 'evaluation_id' in broadcast_data and isinstance(broadcast_data['evaluation_id'], str) == False:
            broadcast_data['evaluation_id'] = str(broadcast_data['evaluation_id'])
        # Ensure enum is converted to string for JSON serialization
        if 'status' in broadcast_data:
            broadcast_data['status'] = evaluation_run.status.value
        broadcast_data["validator_hotkey"] = client.hotkey  # Keep as validator_hotkey for API compatibility
        
        await ws.send_to_all_non_validators("evaluation-run-update", broadcast_data)
        
        return {"status": "success", "message": "Evaluation run stored successfully", "run_id": str(evaluation_run.run_id)}
        
    except Exception as e:
        logger.error(f"Error upserting evaluation run for {client.get_type()} {client.hotkey}: {str(e)}")
        return {"status": "error", "message": f"Failed to upsert evaluation run: {str(e)}"} 