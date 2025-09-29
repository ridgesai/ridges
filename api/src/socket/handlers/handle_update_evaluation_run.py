from typing import TYPE_CHECKING, Dict, Any

from api.src.backend.db_manager import get_transaction
from api.src.backend.entities import Client, EvaluationRun
from api.src.models.evaluation import Evaluation
from api.src.backend.queries.evaluation_runs import all_runs_finished, update_evaluation_run
from loggers.logging_utils import get_logger
from typing import Union

if TYPE_CHECKING:
    from api.src.models.screener import Screener
    from api.src.models.validator import Validator

logger = get_logger(__name__)

async def handle_update_evaluation_run(
    client: Client,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle update-evaluation-run message from a client"""

    logger.info(f"XXXXXXXXXX Received update-evaluation run: run_id {response_json['evaluation_run']['run_id']} --> {response_json['evaluation_run']['status']}")



    # Validate client type
    if client.get_type() not in ["validator", "screener"]:
        logger.error(f"Client {client.ip_address} is not a validator or screener. Ignoring update evaluation run request.")
        return {"status": "error", "message": "Client is not a validator or screener"}
    client: Union["Validator", "Screener"] = client
    
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
        
        # Update screener status from "reserving" to "screening" on first evaluation run update
        if client.get_type() == "screener" and client.status == "reserving":
            old_status = client.status
            client.status = "screening"
            client.current_evaluation_id = evaluation_run.evaluation_id
            logger.info(f"Screener {client.hotkey}: {old_status} -> screening (first evaluation run update)")
        
        # Get connected validators and compute status
        from api.src.models.validator import Validator
        connected_validators = await Validator.get_connected()
        
        # Compute validator status metrics
        total_validators = len(connected_validators)
        available_validators = len([v for v in connected_validators if v.is_available()])
        busy_validators = total_validators - available_validators
        
        validator_status = {
            "total_connected": total_validators,
            "available": available_validators,
            "busy": busy_validators,
            "capacity_utilization": (busy_validators / total_validators) if total_validators > 0 else 0.0
        }
        
        logger.info(f"Validator status after evaluation run update: {validator_status}")
        
        # Broadcast update to connected clients
        from api.src.socket.websocket_manager import WebSocketManager
        ws = WebSocketManager.get_instance()
        
        # Broadcast minimal flow update
        try:
            from api.src.socket.websocket_manager import WebSocketManager
            from api.src.backend.queries.evaluations import get_evaluation_by_evaluation_id
            from datetime import datetime
            
            eval_data = await get_evaluation_by_evaluation_id(evaluation_run.evaluation_id)
            if eval_data:
                await WebSocketManager.get_instance().send_to_all_non_validators(
                    "evaluation_progress",
                    {
                        "version_id": str(eval_data.version_id),
                        "evaluation_id": str(evaluation_run.evaluation_id),
                        "run_id": str(evaluation_run.run_id),
                        "status": evaluation_run.status.value,
                        "timestamp": datetime.now().isoformat()
                    }
                )
        except Exception as e:
            logger.warning(f"Failed to broadcast evaluation progress: {e}")

        if await all_runs_finished(evaluation_run.evaluation_id):
            logger.info(f"All runs finished for evaluation {evaluation_run.evaluation_id}. Finishing evaluation.")
            if client.get_type() == "validator":
                logger.info(f"Calling finish_evaluation for {evaluation_run.evaluation_id}")
                await client.finish_evaluation(evaluation_run.evaluation_id)
                logger.info(f"Called finish_evaluation for {evaluation_run.evaluation_id}")
            elif client.get_type() == "screener":
                logger.info(f"Calling finish_screening for {evaluation_run.evaluation_id}")
                await client.finish_screening(evaluation_run.evaluation_id)
                logger.info(f"Called finish_screening for {evaluation_run.evaluation_id}")
            else:
                logger.info(f"Unknown type, not validator or screener, when trying to finish evaluation {evaluation_run.evaluation_id}")
        
        # Prepare broadcast data
        broadcast_data = evaluation_run.model_dump(mode='json')
        # Ensure enum is converted to string for JSON serialization
        if 'status' in broadcast_data:
            broadcast_data['status'] = evaluation_run.status.value
        broadcast_data["validator_hotkey"] = client.hotkey  # Keep as validator_hotkey for API compatibility
        broadcast_data["progress"] = await Evaluation.get_progress(evaluation_run.evaluation_id)
        broadcast_data["validator_status"] = validator_status  # Include computed validator status
        
        await ws.send_to_all_non_validators("evaluation-run-update", broadcast_data)
        
        return {"status": "success", "message": "Evaluation run stored successfully", "run_id": str(evaluation_run.run_id)}
        
    except Exception as e:
        logger.error(f"Error updating evaluation run for {client.get_type()} {client.hotkey}: {str(e)}")
        return {"status": "error", "message": f"Failed to update evaluation run: {str(e)}"} 