"""Handler for agent version events."""

from datetime import datetime
from validator.utils.logging import get_logger
from validator.sandbox.schema import AgentVersion
from validator.tasks.run_evaluation import run_evaluation
from validator.config import validator_hotkey

logger = get_logger(__name__)

async def handle_evaluation(websocket_app, json_message):
    """Handle agent version events.

    Parameters
    ----------
    websocket_app: WebsocketApp instance for managing websocket connection.
    json_message: parsed JSON payload containing the agent version.
    """

    if websocket_app.evaluation_running.is_set():
        logger.info("Evaluation already running – ignoring agent-version event")
        return

    if json_message.get("evaluation_id", None) is None:
        logger.info("No agent versions left to evaluate")
        return


    logger.info(f"Received evaluation: {json_message}")
    try:
        # Extract agent version data from the response
        evaluation_id = json_message.get("evaluation_id")
        agent_data = json_message.get("agent_version", {})
        agent_id = agent_data.get("agent_id")
        miner_hotkey = agent_data.get("miner_hotkey")
        version_num = agent_data.get("version_num")
        created_at = agent_data.get("created_at")
        version_id = agent_data.get("version_id")

        # Create AgentVersion object
        agent_version = AgentVersion(
            version_id=version_id,
            agent_id=agent_id,
            miner_hotkey=miner_hotkey,
            version_num=version_num,
            created_at=datetime.fromisoformat(created_at),
        )

        await run_evaluation(websocket_app, evaluation_id, agent_version)

        try:
            await websocket_app.send({"event": "get-next-evaluation"})
            logger.info("Requested next agent version after evaluation completion")
        except Exception as e:
            logger.error(f"Failed to request next version: {e}")


    except Exception as e:
        logger.error(f"Error handling agent version: {e}")
        logger.exception("Full error traceback:") 