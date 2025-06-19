"""Websocket utilities for sending evaluation runs."""

import json

from validator.db.schema import EvaluationRun
from shared.logging_utils import get_logger

logger = get_logger(__name__)


async def send_evaluation_run_websocket(websocket_app, evaluation_run: EvaluationRun) -> None:
    """Send an evaluation run through the websocket app.
    
    Args:
        websocket_app: WebsocketApp instance for queueing messages
        evaluation_run: EvaluationRun object to send
    """
    if not websocket_app:
        logger.warning("No websocket app available to send evaluation run")
        return

    try:
        # Convert evaluation run to dictionary
        evaluation_data = {
            "event": "upsert-evaluation-run",
            "evaluation_run": {
                "run_id": evaluation_run.run_id,
                "evaluation_id": evaluation_run.evaluation_id,
                "validator_hotkey": evaluation_run.validator_hotkey,
                "swebench_instance_id": evaluation_run.swebench_instance_id,
                "response": evaluation_run.response,
                "error": evaluation_run.error,
                "fail_to_pass_success": evaluation_run.fail_to_pass_success,
                "pass_to_pass_success": evaluation_run.pass_to_pass_success,
                "fail_to_fail_success": evaluation_run.fail_to_fail_success,
                "pass_to_fail_success": evaluation_run.pass_to_fail_success,
                "solved": evaluation_run.solved,
                "started_at": evaluation_run.started_at.isoformat() if evaluation_run.started_at else None,
                "finished_at": evaluation_run.finished_at.isoformat() if evaluation_run.finished_at else None
            }
        }

        # Schedule the message to be sent
        await websocket_app.send(evaluation_data)
        logger.info(f"Scheduled evaluation run {evaluation_run.run_id} for sending")

    except Exception as e:
        logger.error(f"Failed to schedule evaluation run for sending: {e}")
        logger.exception("Full error traceback:")