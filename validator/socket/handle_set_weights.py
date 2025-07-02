from validator.utils.logging import get_logger
from validator.tasks.set_weights import set_weights

logger = get_logger(__name__)

async def handle_set_weights(websocket_app, json_message):
    """Handle a `set-weights` websocket event.

    Expected payload::

        {
            "event": "set-weights",
            "miner_hotkey": "<MINER_HOTKEY>",
            "version_id": "<VERSION_UUID>",          # for logging
            "avg_score": 0.32                        # for logging
        }

    Only the ``miner_hotkey`` field is mandatory.  The function resolves the miner's UID
    on-chain and sets the validator's weights accordingly (all weight to that miner
    subject to chain constraints).
    """

    hotkey = (
        json_message.get("hotkey")
        or json_message.get("miner_hotkey")
        or json_message.get("best_miner_hotkey")
    )
    if hotkey is None:
        logger.error("Received set-weights event without a 'hotkey' field – ignoring")
        return

    version_id = json_message.get("version_id")
    avg_score = json_message.get("avg_score")

    logger.info(
        f"Received set-weights event – hotkey={hotkey}, version_id={version_id}, avg_score={avg_score}"
    )

    try:
        await set_weights(best_miner_hotkey=hotkey)
        logger.info(f"Successfully processed set-weights event for hotkey {hotkey}")
    except Exception as e:
        logger.error(f"Failed to process set-weights event for hotkey {hotkey}: {e}") 