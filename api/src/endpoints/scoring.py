from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Form
from pydantic import BaseModel
import logging
import uuid
from datetime import datetime, timedelta
import ast
import sys

from fiber import Keypair

from api.src.utils.config import PERMISSABLE_PACKAGES, AGENT_RATE_LIMIT_SECONDS
from api.src.utils.auth import verify_request
from api.src.utils.models import Agent, TopAgentHotkey
from api.src.db.operations import DatabaseManager
from api.src.socket.websocket_manager import WebSocketManager

db = DatabaseManager()

logger = logging.getLogger(__name__)

async def weight_receiving_agent():
    '''
    This is used to compute the current best agent. Validators can rely on this or keep a local database to compute this themselves.
    The method looks at the highest scored agents that have been considered by at least two validators. If they are within 3% of each other, it returns the oldest one
    This will be deprecated shortly in favor of validators posting weight themselves
    ''' 
    top_agent: TopAgentHotkey = db.get_top_agent()

    return top_agent


router = APIRouter()

routes = [
    ("/weights", weight_receiving_agent),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["scoring"],
        dependencies=[Depends(verify_request)],
        methods=["GET"]
    )
