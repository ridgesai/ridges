from fiber.chain.chain_utils import load_hotkey_keypair
from typing import Any

from validator.config import WALLET_NAME, HOTKEY_NAME

validator_hotkey = load_hotkey_keypair(WALLET_NAME, HOTKEY_NAME)

def sign_validator_message(
    message_type: str,
    payload: dict[str, Any]
):
    match message_type:
        case "validator-info":
            pass

        case "start-evaluation":
            pass

        case "evaluation_run-upsert":
            pass 


    # message = f"validator-auth:{validator_hotkey.ss58_address}:{VERSION_COMMIT_HASH}:{timestamp}"
    # return validator_hotkey.sign(message).hex()
    return "fakesig"

def verify_validator_signature(
    message_type: str,
    signature: str,
    message: None
):
    return True
    pass