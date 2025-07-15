from datetime import datetime
from typing import Dict, Any
from fastapi import WebSocket

from loggers.logging_utils import get_logger
from api.src.backend.queries.evaluation_runs import store_evaluation_run
from api.src.backend.entities import EvaluationRun, SandboxStatus

logger = get_logger(__name__)

async def handle_upsert_evaluation_run(
    websocket: WebSocket,
    validator_hotkey: str,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle upsert-evaluation-run message from a validator"""
    evaluation_run_data = response_json["evaluation_run"]
    
    logger.info(f"Validator with hotkey {validator_hotkey} sent an evaluation run. Upserting evaluation run.")
    
    try:
        def parse_datetime(dt_str):
            """Parse datetime string to datetime object, return None if None or empty"""
            if not dt_str:
                return None
            if isinstance(dt_str, datetime):
                return dt_str
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        
        evaluation_run = EvaluationRun(
            run_id=evaluation_run_data["run_id"],
            evaluation_id=evaluation_run_data["evaluation_id"],
            swebench_instance_id=evaluation_run_data["swebench_instance_id"],
            status=SandboxStatus(evaluation_run_data["status"]),
            response=evaluation_run_data["response"],
            error=evaluation_run_data["error"],
            pass_to_fail_success=evaluation_run_data["pass_to_fail_success"],
            fail_to_pass_success=evaluation_run_data["fail_to_pass_success"],
            pass_to_pass_success=evaluation_run_data["pass_to_pass_success"],
            fail_to_fail_success=evaluation_run_data["fail_to_fail_success"],
            solved=evaluation_run_data["solved"],
            started_at=parse_datetime(evaluation_run_data["started_at"]),
            sandbox_created_at=parse_datetime(evaluation_run_data["sandbox_created_at"]),
            patch_generated_at=parse_datetime(evaluation_run_data["patch_generated_at"]),
            eval_started_at=parse_datetime(evaluation_run_data["eval_started_at"]),
            result_scored_at=parse_datetime(evaluation_run_data["result_scored_at"]),
            cancelled_at=parse_datetime(evaluation_run_data.get("cancelled_at"))
        )
        await store_evaluation_run(evaluation_run)
        
        # Create a dictionary for broadcasting that includes the validator_hotkey
        broadcast_data = evaluation_run.model_dump()
        broadcast_data["validator_hotkey"] = validator_hotkey
        return broadcast_data
        
    except Exception as e:
        logger.error(f"Error upserting evaluation run: {str(e)}")
        return {"error": f"Failed to upsert evaluation run: {str(e)}"} 