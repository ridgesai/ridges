"""Handler for agent version events."""

import asyncio
from datetime import datetime
import json
from shared.logging_utils import get_logger
from validator.db.schema import AgentVersion
from validator.dependancies import get_session_factory
from sqlalchemy.orm import Session
from validator.tasks.evaluate_agent_version import evaluate_agent_version
from validator.config import validator_hotkey

logger = get_logger(__name__)


async def handle_agent_for_evaluation(websocket, json_message, evaluation_running: asyncio.Event):
    """Handle agent version events.

    Parameters
    ----------
    websocket: active websocket connection to the platform.
    json_message: parsed JSON payload containing the agent version.
    evaluation_running: Event signalling if an evaluation is running.
    """

    if evaluation_running.is_set():
        logger.info("Evaluation already running – ignoring agent-version event")
        return

    if json_message.get("error", None) is not None:
        logger.info("No agent versions left to evaluate")
        return

    logger.info(f"Received agent version: {json_message}")
    try:
        # Extract agent version data from the response
        agent_data = json_message.get("agent_version", {})
        agent_id = agent_data.get("agent_id")
        miner_hotkey = agent_data.get("miner_hotkey")
        version_num = agent_data.get("version_num")
        created_at = agent_data.get("created_at")
        version_id = agent_data.get("version_id")

        if not all([version_id, agent_id, miner_hotkey, version_num, created_at]):
            logger.error(f"Missing required fields in agent version response: {agent_data}")
            return

        # Create AgentVersion object
        agent_version = AgentVersion(
            version_id=version_id,
            agent_id=agent_id,
            miner_hotkey=miner_hotkey,
            version_num=version_num,
            created_at=datetime.fromisoformat(created_at),
        )

        # # Save to database
        # SessionFactory = get_session_factory()
        # session = SessionFactory()
        # try:
        #     session.add(agent_version)
        #     session.commit()
        #     logger.info(f"Saved agent version to database: {agent_version.version_id}")
        # finally:
        #     session.close()

        # Start evaluation task with websocket as first argument
        task = asyncio.create_task(
            evaluate_agent_version(websocket, agent_version, evaluation_running)
        )

        async def _on_done(_):
            req = {
                "event": "get-next-version",
                "validator_hotkey": validator_hotkey.ss58_address,
            }
            try:
                await websocket.send(json.dumps(req))
                logger.info("Requested next agent version after evaluation completion")
            except Exception as e:
                logger.error(f"Failed to request next version: {e}")

        task.add_done_callback(lambda t: asyncio.create_task(_on_done(t)))

    except Exception as e:
        logger.error(f"Error handling agent version: {e}")
        logger.exception("Full error traceback:") 