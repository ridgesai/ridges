from typing import Dict, Any

from api.src.backend.db_manager import get_transaction
from api.src.backend.entities import Client, EvaluationRun
from api.src.models.evaluation import Evaluation
from api.src.backend.queries.evaluation_runs import all_runs_finished, update_evaluation_run
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def handle_update_evaluation_run(
    client: Client,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle update-evaluation-run message from a client"""
    # Validate client type
    if client.get_type() not in ["validator", "screener"]:
        logger.error(f"Client {client.ip_address} is not a validator or screener. Ignoring update evaluation run request.")
        return {"status": "error", "message": "Client is not a validator or screener"}
    
    evaluation_run_data = response_json.get("evaluation_run")
    
    if not evaluation_run_data:
        return {"status": "error", "message": "Missing evaluation_run data"}
    
    try:
        logger.info(f"{client.get_type().title()} {client.hotkey} sent an evaluation run. Updating evaluation run.")
        
        # Convert to EvaluationRun object and store
        evaluation_run = EvaluationRun(**evaluation_run_data)
        await update_evaluation_run(evaluation_run)
        
        # Broadcast update to connected clients
        from api.src.socket.websocket_manager import WebSocketManager
        ws = WebSocketManager.get_instance()

        if await all_runs_finished(evaluation_run.evaluation_id):
            logger.info(f"All runs finished for evaluation {evaluation_run.evaluation_id}. Finishing evaluation.")
            evaluation = await Evaluation.get_by_id(str(evaluation_run.evaluation_id))
            if evaluation:
                async with get_transaction() as conn:
                    await evaluation.finish(conn)
        
        # Prepare broadcast data
        broadcast_data = evaluation_run.model_dump(mode='json')
        broadcast_data["validator_hotkey"] = client.hotkey  # Keep as validator_hotkey for API compatibility
        
        await ws.send_to_all_non_validators("evaluation-run-update", broadcast_data)
        
        return {"status": "success", "message": "Evaluation run stored successfully", "run_id": str(evaluation_run.run_id)}
        
    except Exception as e:
        logger.error(f"Error updating evaluation run for {client.get_type()} {client.hotkey}: {str(e)}")
        return {"status": "error", "message": f"Failed to upsert evaluation run: {str(e)}"} 