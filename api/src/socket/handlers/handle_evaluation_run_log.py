from typing import Dict, Any
from api.src.backend.db_manager import get_transaction
from api.src.backend.entities import Client
from api.src.backend.queries.evaluation_runs import insert_evaluation_run_log, insert_evaluation_run_logs_batch
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def handle_evaluation_run_log(
    client: Client,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle evaluation-run-log message from a validator"""
    # Validate client type
    if client.get_type() not in ["validator", "screener"]:
        logger.error(f"Client {client.ip_address} is not a validator or screener. Ignoring evaluation run log.")
        return {"status": "error", "message": "Client is not a validator or screener"}
    
    run_id = response_json.get("run_id")
    log_line = response_json.get("line")
    
    if not run_id or not log_line:
        return {"status": "error", "message": "Missing run_id or line data"}
    
    try:
        logger.debug(f"Validator {client.hotkey} sent log line for run {run_id}")
        
        # Store log line in database
        await insert_evaluation_run_log(run_id, log_line)
        
        return {"status": "success", "message": "Log line stored successfully"}
        
    except Exception as e:
        logger.error(f"Error storing evaluation run log for validator {client.hotkey}: {str(e)}")
        return {"status": "error", "message": f"Failed to store log line: {str(e)}"}

async def handle_evaluation_run_logs_batch(
    client: Client,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle evaluation-run-logs-batch message from a validator"""
    # Validate client type
    if client.get_type() not in ["validator", "screener"]:
        logger.error(f"Client {client.ip_address} is not a validator or screener. Ignoring batch evaluation run logs.")
        return {"status": "error", "message": "Client is not a validator or screener"}
    
    run_id = response_json.get("run_id")
    logs = response_json.get("logs", [])
    
    if not run_id or not logs:
        return {"status": "error", "message": "Missing run_id or logs data"}
    
    try:
        logger.debug(f"Validator {client.hotkey} sent {len(logs)} batch logs for run {run_id}")
        
        # Store all log lines in database as a batch
        await insert_evaluation_run_logs_batch(run_id, logs)
        
        return {"status": "success", "message": f"Stored {len(logs)} log lines successfully"}
        
    except Exception as e:
        logger.error(f"Error storing batch evaluation run logs for validator {client.hotkey}: {str(e)}")
        return {"status": "error", "message": f"Failed to store batch log lines: {str(e)}"}