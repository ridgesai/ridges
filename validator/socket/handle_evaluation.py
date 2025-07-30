"""Handler for agent version events."""

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING
from loggers.logging_utils import get_logger
from validator.sandbox.schema import AgentVersion, EvaluationRun
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
        evaluation_runs = [EvaluationRun(**run) for run in json_message.get("evaluation_runs", [])]

        agent_version = AgentVersion(
            version_id=version_id,
            miner_hotkey=miner_hotkey,
            version_num=version_num,
            created_at=datetime.fromisoformat(created_at),
        )

        # Create and track the evaluation task
        websocket_app.evaluation_task = asyncio.create_task(
            run_evaluation(websocket_app, evaluation_id, agent_version, evaluation_runs)
        )

        await websocket_app.evaluation_task

    except asyncio.CancelledError:
        logger.info("Evaluation task was cancelled")
    except Exception as e:
        logger.error(f"Error handling agent version: {e}")
        logger.exception("Full error traceback:")
    finally:
        websocket_app.evaluation_task = None
