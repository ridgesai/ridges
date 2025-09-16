from fiber import Keypair
from new_validator.utils.messaging import Authentication, FinishEvaluation, StartEvaluation, UpsertEvaluationRun, ValidatorMessage
from logging import getLogger 

logger = getLogger(__name__)

def construct_validator_signature_payload(payload: ValidatorMessage) -> str:
    match payload:
        case Authentication():
            message = f"validator-auth:{payload.validator_hotkey}:{payload.version_commit_hash}:{payload.timestamp}"
            
        case StartEvaluation():
            message = f"start-eval:{payload.evaluation_id}:{payload.validator_hotkey}:{payload.version_commit_hash}"
            
        case UpsertEvaluationRun():
            message = f"eval-run:{payload.evaluation_id}:{payload.run_id}:{payload.status.value}"
            
        case FinishEvaluation():
            message = f"finish-eval:{payload.evaluation_id}:{payload.final_score}"
            
        case _:
            raise ValueError(f"Unknown message type: {type(payload).__name__}")

    return message

def sign_validator_message(validator_hotkey: Keypair, payload: ValidatorMessage) -> str:
    message = construct_validator_signature_payload(payload=payload)
    return sign(validator_hotkey=validator_hotkey, message=message)

def sign(validator_hotkey: Keypair, message: str) -> str:
    return validator_hotkey.sign(message).hex()

def verify_validator_signaure(payload: ValidatorMessage, validator_hotkey: str):
    if not (payload.signature):
        raise Exception("No signature in payload")

    message = construct_validator_signature_payload(payload=payload)
    keypair = Keypair(public_key=bytes.fromhex(validator_hotkey), ss58_format=42)
    
    if not keypair.verify(message, payload.signature):
        logger.error("Invalid signature for validator request")
        return False

    return True