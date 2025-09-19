"""Handler for agent version events."""

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING
from loggers.logging_utils import get_logger
from validator.tasks.run_evaluation import run_evaluation
from validator.config import SCREENER_MODE, validator_hotkey
from ddtrace import tracer

if TYPE_CHECKING:
    from validator.socket.websocket_app import WebsocketApp

logger = get_logger(__name__)

@tracer.wrap(resource="handle-evaluation")
async def handle_evaluation(websocket_app: "WebsocketApp", json_message: dict):
    """Handle agent version events.

    Parameters
    ----------
    websocket_app: WebsocketApp instance for managing websocket connection.
    json_message: parsed JSON payload containing the agent version.
    """

    if websocket_app.evaluation_task is not None:
        logger.info("Evaluation already running – ignoring agent-version event")
        return

    if json_message.get("evaluation_id", None) is None:
        logger.info("No agent versions left to evaluate")
        return

    try:
        # Extract agent version data from the response
        evaluation_id = json_message.get("evaluation_id")
        agent_data = json_message.get("agent_version", {})
        miner_hotkey = agent_data.get("miner_hotkey")
        version_num = agent_data.get("version_num")
        created_at = agent_data.get("created_at")
        version_id = agent_data.get("version_id")
        evaluation_runs = json_message.get("evaluation_runs", [])


        logger.info("CXII: handle_evaluation()");
        logger.info(f"CXII:     evaluation_id: {evaluation_id}");
        # logger.info(f"CXII:     agent_data: {agent_data}");
        # logger.info(f"CXII:     miner_hotkey: {miner_hotkey}");
        # logger.info(f"CXII:     version_num: {version_num}");
        # logger.info(f"CXII:     created_at: {created_at}");
        logger.info(f"CXII:     version_id: {version_id}");
        logger.info(f"CXII:     EVAL RUNS");
        for eval_run in evaluation_runs:
            logger.info(f"CXII:         {eval_run['run_id']} -- {eval_run['swebench_instance_id']}");
        # logger.info(f"CXII:     evaluation_runs: {evaluation_runs}");

        # Handle 'Z' timezone suffix for UTC
        if created_at.endswith('Z'):
            created_at = created_at[:-1] + '+00:00'

        # Create and track the evaluation task
        websocket_app.evaluation_task = asyncio.create_task(
            run_evaluation(websocket_app, evaluation_id, version_id, evaluation_runs)
        )

        # await websocket_app.evaluation_task

    except asyncio.CancelledError:
        logger.info("Evaluation task was cancelled. Ensure you are running docker.")
    except Exception as e:
        logger.error(f"Error handling agent version: {e}")
        logger.exception("Full error traceback:")
    finally:
        websocket_app.evaluation_task = None
