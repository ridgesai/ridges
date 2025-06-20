from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
import logging
import boto3
import os
from dotenv import load_dotenv

from api.src.utils.auth import verify_request
from api.src.db.operations import DatabaseManager
from api.src.socket.server import WebSocketServer

logger = logging.getLogger(__name__)

db = DatabaseManager()
server = WebSocketServer()

load_dotenv()
s3_bucket_name = os.getenv('AWS_S3_BUCKET_NAME')

# Get version file
async def get_agent_version_file(version_id: str):
    agent_version = db.get_agent_version(version_id)
    
    if not agent_version:
        logger.info(f"File for agent version {version_id} was requested but not found in our database")
        raise HTTPException(
            status_code=404, 
            detail="The requested agent version was not found. Are you sure you have the correct version ID?"
        )
    
    try:
        s3 = boto3.client('s3')
        agent_object = s3.get_object(Bucket=s3_bucket_name, Key=f"{version_id}/agent.py")
    except Exception as e:
        logger.error(f"Error retrieving agent version file from S3 for version {version_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving agent version file. Please try again later."
        )
    
    headers = {
        "Content-Disposition": f'attachment; filename="agent.py"'
    }
    return StreamingResponse(agent_object['Body'], media_type='application/octet-stream', headers=headers)

async def get_validator_versions():
    return server.validator_versions

router = APIRouter()

routes = [
    ("/agent-version-file", get_agent_version_file),
    ("/validator-versions", get_validator_versions),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["retrieval"],
        dependencies=[Depends(verify_request)],
        methods=["GET"]
    )
