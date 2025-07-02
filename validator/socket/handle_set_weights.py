from validator.utils.logging import get_logger
from validator.tasks.set_weights import set_weights

logger = get_logger(__name__)

async def handle_set_weights(websocket_app, json_message):
    """Handle a `set-weights` websocket event.

    Expected payload::

        {
            "event": "set-weights",
            "hotkey": "<MINER_HOTKEY>"
        }

    Only the ``hotkey`` field is mandatory.  The function resolves the miner's UID
    on-chain and sets the validator's weights accordingly (all weight to that miner
    subject to chain constraints).
    """

    hotkey = json_message.get("hotkey") or json_message.get("best_miner_hotkey")
    if hotkey is None:
        logger.error("Received set-weights event without a 'hotkey' field – ignoring")
        return

    logger.info(f"Received set-weights event for hotkey {hotkey}")

    try:
        await set_weights(best_miner_hotkey=hotkey)
        logger.info(f"Successfully processed set-weights event for hotkey {hotkey}")
    except Exception as e:
        logger.error(f"Failed to process set-weights event for hotkey {hotkey}: {e}") 