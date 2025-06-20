"""Handler for agent version events."""

from datetime import datetime
from shared.logging_utils import get_logger
from validator.db.schema import AgentVersion
from validator.tasks.evaluate_agent_version import evaluate_agent_version
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

    websocket_app.evaluation_running.set()

    if json_message.get("error", None) is not None:
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

        # # Save to database
        # SessionFactory = get_session_factory()
        # session = SessionFactory()
        # try:
        #     session.add(agent_version)
        #     session.commit()
        #     logger.info(f"Saved agent version to database: {agent_version.version_id}")
        # finally:
        #     session.close()

        await evaluate_agent_version(websocket_app, evaluation_id, agent_version)
        websocket_app.evaluation_running.clear()

        try:
            await websocket_app.send({"event": "get-next-evaluation"})
            logger.info("Requested next agent version after evaluation completion")
        except Exception as e:
            logger.error(f"Failed to request next version: {e}")


    except Exception as e:
        logger.error(f"Error handling agent version: {e}")
        logger.exception("Full error traceback:") 