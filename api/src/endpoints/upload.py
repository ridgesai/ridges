from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
import logging
import uuid
from datetime import datetime, timedelta
import boto3
import os
import ast
import sys
from dotenv import load_dotenv

from api.src.utils.config import PERMISSABLE_PACKAGES, AGENT_RATE_LIMIT_SECONDS
from api.src.utils.auth import verify_request
from api.src.utils.models import Agent, AgentVersion
from api.src.db.operations import DatabaseManager
from api.src.socket.server import WebSocketServer

logger = logging.getLogger(__name__)

load_dotenv()

s3_bucket_name = os.getenv('AWS_S3_BUCKET_NAME')

db = DatabaseManager()
server = WebSocketServer()

async def post_agent (
    agent_file: UploadFile = File(...),
    miner_hotkey: str = None,
):
    # Check if miner_hotkey is provided
    if not miner_hotkey:
        raise HTTPException(
            status_code=400,
            detail="miner_hotkey is required"
        )
    
    existing_agent = db.get_agent_by_hotkey(miner_hotkey)
    if existing_agent and existing_agent.last_updated > datetime.now() - timedelta(seconds=AGENT_RATE_LIMIT_SECONDS):
        raise HTTPException(
            status_code=400,
            detail=f"You must wait {AGENT_RATE_LIMIT_SECONDS} seconds before uploading a new agent"
        )

    # Check filename
    if agent_file.filename != "agent.py":
        raise HTTPException(
            status_code=400,
            detail="File must be a python file named agent.py"
        )
    
    # Check file size
    MAX_FILE_SIZE = 1 * 1024 * 1024  # 1MB in bytes
    file_size = 0
    content = b""
    for chunk in agent_file.file:
        file_size += len(chunk)
        content += chunk
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail="File size must not exceed 1MB"
            )
    # Reset file pointer
    await agent_file.seek(0)
    
    # Check if file is a valid python file
    try:
        # Parse the file content
        tree = ast.parse(content.decode('utf-8'))
        
        # Check for if __name__ == "__main__"
        has_main_check = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                if isinstance(node.test, ast.Compare):
                    if isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__":
                        if len(node.test.ops) == 1 and isinstance(node.test.ops[0], ast.Eq):
                            if isinstance(node.test.comparators[0], ast.Constant) and node.test.comparators[0].value == "__main__":
                                has_main_check = True
                                break
        
        if not has_main_check:
            raise HTTPException(
                status_code=400,
                detail='File must contain "if __name__ == "__main__":"'
            )
        
        # Check imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    # Allow standard library packages (those that don't need pip install) and approved packages
                    if name.name in sys.stdlib_module_names or name.name in PERMISSABLE_PACKAGES:
                        continue
                    raise HTTPException(
                        status_code=400,
                        detail=f"Import '{name.name}' is not allowed. Only standard library and approved packages are permitted."
                    )
            elif isinstance(node, ast.ImportFrom):
                # Allow standard library packages (those that don't need pip install) and approved packages
                if node.module in sys.stdlib_module_names or node.module in PERMISSABLE_PACKAGES:
                    continue
                raise HTTPException(
                    status_code=400,
                    detail=f"Import from '{node.module}' is not allowed. Only standard library and approved packages are permitted."
                )

    except SyntaxError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid Python syntax: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error validating the agent file: {str(e)}"
        )
        
    agent_id = str(uuid.uuid4()) if not existing_agent else existing_agent.agent_id
    version_id = str(uuid.uuid4())
    
    s3_client = boto3.client('s3')

    try:
        s3_client.upload_fileobj(agent_file.file, s3_bucket_name, f"{version_id}/agent.py")
    except Exception as e:
        logger.error(f"Failed to upload agent version to S3: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to store agent version in our database. Please try again later."
        )
    
    agent_object = Agent(
        agent_id=agent_id,
        miner_hotkey=existing_agent.miner_hotkey if existing_agent else miner_hotkey,
        latest_version=existing_agent.latest_version + 1 if existing_agent else 0,
        created_at=existing_agent.created_at if existing_agent else datetime.now(),
        last_updated=datetime.now(),
    )
    result = db.store_agent(agent_object)
    if result == 0:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to store agent version in our database. Please try again later."
        )
    
    agent_version_object = AgentVersion(
        version_id=version_id,
        agent_id=agent_id,
        version_num=agent_object.latest_version,
        created_at=datetime.now(),
        score=None
    )
    result = db.store_agent_version(agent_version_object)
    if result == 0:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to store agent version in our database. Please try again later."
        )
    
    await server.create_new_evaluations(version_id)

    return {
        "status": "success",
        "message": f"Successfully updated agent {agent_id} to version {agent_object.latest_version}" if existing_agent else f"Successfully created agent {agent_id}"
    } 

router = APIRouter()

routes = [
    ("/agent", post_agent),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["upload"],
        dependencies=[Depends(verify_request)],
        methods=["POST"]
    )
