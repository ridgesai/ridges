from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from typing import List
import logging
from datetime import datetime

from api.src.utils.auth import verify_request
from api.src.utils.models import ValidatorLog
from api.src.db.operations import DatabaseManager

logger = logging.getLogger(__name__)
db = DatabaseManager()

class LogDrainRequest(BaseModel):
    validator_hotkey: str
    logs: List[dict]  # List of log entries from validator's logging.db

async def post_log_drain(request: LogDrainRequest = Body(...)):
    """
    Endpoint to receive log data from validators.
    Each validator sends their logs with their hotkey.
    """
    try:
        logs_stored = 0
        logs_failed = 0
        
        for log_entry in request.logs:
            try:
                # Convert the log entry from validator's logging.db format to our ValidatorLog model
                validator_log = ValidatorLog(
                    original_log_id=log_entry['id'],
                    validator_hotkey=request.validator_hotkey,
                    timestamp=datetime.fromisoformat(log_entry['timestamp'].replace('Z', '+00:00')) if isinstance(log_entry['timestamp'], str) else log_entry['timestamp'],
                    levelname=log_entry['levelname'],
                    name=log_entry['name'],
                    pathname=log_entry['pathname'],
                    funcName=log_entry['funcName'],
                    lineno=log_entry['lineno'],
                    message=log_entry['message'],
                    active_coroutines=log_entry['active_coroutines'],
                    eval_loop_num=log_entry['eval_loop_num']
                )
                
                result = db.store_validator_log(validator_log)
                if result == 1:
                    logs_stored += 1
                else:
                    logs_failed += 1
                    
            except Exception as e:
                logger.error(f"Error processing log entry {log_entry.get('id', 'unknown')}: {str(e)}")
                logs_failed += 1
                continue
        
        logger.info(f"Log drain from {request.validator_hotkey}: {logs_stored} stored, {logs_failed} failed")
        
        return {
            "status": "success",
            "message": f"Processed {len(request.logs)} log entries",
            "logs_stored": logs_stored,
            "logs_failed": logs_failed
        }
        
    except Exception as e:
        logger.error(f"Error in log drain endpoint: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process log drain request: {str(e)}"
        )

router = APIRouter()

routes = [
    ("/logs", post_log_drain),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["log_drain"],
        dependencies=[Depends(verify_request)],
        methods=["POST"]
    )
