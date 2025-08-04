from typing import TYPE_CHECKING, Dict, Any

from api.src.backend.db_manager import get_transaction
from api.src.backend.entities import Client, EvaluationRun
from api.src.models.evaluation import Evaluation
from api.src.backend.queries.evaluation_runs import all_runs_finished, update_evaluation_run
from loggers.logging_utils import get_logger

if TYPE_CHECKING:
    from api.src.models.screener import Screener
    from api.src.models.validator import Validator

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
    client: "Validator" | "Screener" = client
    
    evaluation_run_data = response_json.get("evaluation_run")
    
    if not evaluation_run_data:
        return {"status": "error", "message": "Missing evaluation_run data"}
    
    try:
        logger.info(f"{client.get_type().title()} {client.hotkey} sent an evaluation run. Updating evaluation run.")
        
        # Defensive fix: Handle cases where response field is accidentally a list instead of string
        if isinstance(evaluation_run_data.get("response"), list):
            logger.warning(f"Response field is a list instead of string for {client.hotkey}, converting to string")
            evaluation_run_data["response"] = "\n".join(str(item) for item in evaluation_run_data["response"])
        
        # Convert to EvaluationRun object and store
        evaluation_run = EvaluationRun(**evaluation_run_data)
        await update_evaluation_run(evaluation_run)
        
        # Broadcast update to connected clients
        from api.src.socket.websocket_manager import WebSocketManager
        ws = WebSocketManager.get_instance()

        if await all_runs_finished(evaluation_run.evaluation_id):
            logger.info(f"All runs finished for evaluation {evaluation_run.evaluation_id}. Finishing evaluation.")
            if client.get_type() == "validator":
                await client.finish_evaluation(evaluation_run.evaluation_id)
            elif client.get_type() == "screener":
                await client.finish_screening(evaluation_run.evaluation_id)
        
        # Prepare broadcast data
        broadcast_data = evaluation_run.model_dump(mode='json')
        broadcast_data["validator_hotkey"] = client.hotkey  # Keep as validator_hotkey for API compatibility
        broadcast_data["progress"] = await Evaluation.get_progress(evaluation_run.evaluation_id)
        
        await ws.send_to_all_non_validators("evaluation-run-update", broadcast_data)
        
        return {"status": "success", "message": "Evaluation run stored successfully", "run_id": str(evaluation_run.run_id)}
        
    except Exception as e:
        logger.error(f"Error updating evaluation run for {client.get_type()} {client.hotkey}: {str(e)}", exc_info=True)
        return {"status": "error", "message": f"Failed to update evaluation run: {str(e)}"} 