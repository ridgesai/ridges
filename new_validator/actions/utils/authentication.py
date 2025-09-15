from fiber import Keypair
from new_validator.actions.utils.messaging import Authentication, FinishEvaluation, StartEvaluation, UpsertEvaluationRun, ValidatorMessage

def sign_validator_message(payload: ValidatorMessage, validator_hotkey: Keypair) -> str:
    message: str = ""
    
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

    if not message:
        raise Exception("No message constructed")
    
    return validator_hotkey.sign(message).hex()