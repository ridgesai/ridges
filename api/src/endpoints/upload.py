from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Form
from pydantic import BaseModel
import logging
import uuid
from datetime import datetime, timedelta

from fiber import Keypair

from api.src.utils.config import PERMISSABLE_PACKAGES, AGENT_RATE_LIMIT_SECONDS
from api.src.utils.auth import verify_request
from api.src.db.sqlalchemy_models import Agent, AgentVersion
from api.src.db.operations import DatabaseManager
from api.src.socket.websocket_manager import WebSocketManager
from api.src.db.s3 import S3Manager
from api.src.utils.nodes import get_subnet_hotkeys
from api.src.utils.code_checks import AgentCodeChecker, CheckError

logger = logging.getLogger(__name__)

s3_manager = S3Manager()
db = DatabaseManager()

class AgentUploadRequest(BaseModel):
    public_key: str
    file_info: str
    signature: str
    name: str

# For now, this is a manual process, but will be updated shortly to be automatic
banned_hotkeys = [
    "5GWz1uK6jhmMbPK42dXvyepzq4gzorG1Km3NTMdyDGHaFDe9", 
    "5Dyghsz26XyzWQ4mqrngfGeidM2FUp28Hpzp2Q4sAW21mFkx", 
    "5G6c6QvYnVS5nefgqoyq1rvKCzd3gzCQv2n6dmfLKetyVrwh", 
    "5EXLAJe1tZhaQWtsqp2DKdKpZ2oQ3WBYapUSXFCe9zgvy8Uk", 
    "5E7s2xNMzXKnurpsifmWxojcFa44NkuLPn1U7s9zvVrFjYKb",
    "5F25Xcddj2w3ry5DW1CbguUfsHiPUuq2uHdrYLCueJUBFBfZ",
    "5Gzpc9XTpDUtFkb4NcuJPxrb1C4nybu29RyjF5Mi7uSPPjgU",
    "5GBsbxyQvs78dDJj8p1qMjUYTdQGpAeKksfbHMn5HenntzGA",
    "5Dy33595c9dqwtyXm4CYHu4bfGNYgVGY34ARbktNNgLR4MBQ", 
    "5EL5k31Wm9N74WbUG7SwTCHCbD31ERGH1yBmFsLRtvGjvtvN",
    "5CwU1yR9SXiayopaHPNSU7ony5A1xteyd4S88cNZcys8Uzsu",
    "5HCJjSzoraw8VKHpvpstGCExxNWzuG8hLW54rvFABZtnHjz2",
    "5H3j2JfvX6BJdESAoH6iRUvKkBx83gxyqizwezJyYCuyuW59",
    "5FKbTsEvmYrW9yWf65E2nRjo13Lb6zMxnaWWVzc41BbKrkYm",
    "5F98ZSxBTBKUgvheKeUnS2KkmNVo74EUDNgAJHhSiy1sdjjw",
    "5GbDzWhTz18xMocRpkEkmACznFtppPWkQRNXFhJyAd6p3XYe",
    "5EtFbtr7wW4cAWCHcD1SLMBnExoAeKPg2eEDnzh8amjD8aPT",
    "5DhUkZwxYygbLwHdzPnZT2QAhKE3E5fkeey6pcYRhmBPhs9G",
    "5EcenAqMksipQZJ4x6xe9YbeXLCfMqba5z6URC1K4x1ZP9mT",
    "5F25Xcddj2w3ry5DW1CbguUfsHiPUuq2uHdrYLCueJUBFBfZ",
    "5Gzpc9XTpDUtFkb4NcuJPxrb1C4nybu29RyjF5Mi7uSPPjgU",
    "5GBsbxyQvs78dDJj8p1qMjUYTdQGpAeKksfbHMn5HenntzGA",
    "5Dy33595c9dqwtyXm4CYHu4bfGNYgVGY34ARbktNNgLR4MBQ", 
    "5EL5k31Wm9N74WbUG7SwTCHCbD31ERGH1yBmFsLRtvGjvtvN",
    "5CwU1yR9SXiayopaHPNSU7ony5A1xteyd4S88cNZcys8Uzsu",
    "5HCJjSzoraw8VKHpvpstGCExxNWzuG8hLW54rvFABZtnHjz2",
    "5H3j2JfvX6BJdESAoH6iRUvKkBx83gxyqizwezJyYCuyuW59",
    "5FKbTsEvmYrW9yWf65E2nRjo13Lb6zMxnaWWVzc41BbKrkYm",
    "5F98ZSxBTBKUgvheKeUnS2KkmNVo74EUDNgAJHhSiy1sdjjw",
    "5GbDzWhTz18xMocRpkEkmACznFtppPWkQRNXFhJyAd6p3XYe",
    "5GsKVvwQ4gk9QDk6bb8qSycPJoUcACGmfrQNjxRCE2Ue9wd3",
    "5DS6D4MEMoPC8VSpgDWq3rVqFkjNiQoj5toVCSLA1gak7GZL",
    "5EFKREedPd7vjBWmieRotMpLcpEqR37UjWFAUX6FmvkaN7zq",
    "5Df5ukRD5fnDQZcMMqBkDaQ7dPhQXsfFrkD3s477eFqgPYZh",
    "5EFKREedPd7vjBWmieRotMpLcpEqR37UjWFAUX6FmvkaN7zq",
    "5GP2KhQHKbzcWaW61buSTHtQyEJvcNB9qx9WfiRbtPzs3WfG",
    "5CB7jfYUT2Z4tXFWUmKbGqj3gdTRxXFPg7cATxvCYYn7tBt9",
    "5E812wJwEpcd12FcYhdv21CoMPotpmXsmbQgepmFs5jDLCzv",
    "5E4VUFyLbSgTGmy6Kd5eAb3UFabTX3VuT9M2CY2my8Dv2oRx",
    "5H3frA14nyVf5J1YYvS3qcKc1fwg2kbz997nE9uNyYLcEXSA",
    "5CDqtDptJ1JuAh1hX7pVoPMUXTVjdik2fb1RhybbxgjyUG9Y",
    "5EWjt1HQWxYsFnygJWcZug29KXB9RNTDoCJyZHKThtQSvVvi",
    "5H97WhmqAKj9vsE1K6PFLZTm48zAqTagrjuL59nZwqDu3cZK",
    "5Ge7MjqKKND7g2aTydrdrR82NCZPgF6DEMz4UZoRwwFuUxf4",
    "5DHYBuwoHoVDwL8awnhhJg2oCX5xqLzsoU9ZMEg3g1gdtkXc",
    "5FLQp1rxNiDiCA2Bwna5AUu1eScJf6hT1JtfEMfFeWio1MYz",
    "5HKzmZ1h9CGmyBxAfESBgMPXJKThTMxCB4vwBn8CEVq7GvgG",
    "5Ge7MjqKKND7g2aTydrdrR82NCZPgF6DEMz4UZoRwwFuUxf4",
    "5EqVMYTTxnuqbxzJ19Y7tcu2CbmjG3unuZmreB6c1ksa4q1f",
    "5FX1koxncfZpeDqB8fFQhae52wX4z8q8hEHceb46W6upscc4",
    "5HmwgHVLSvmVxqA2BQ7z9xyedCTdqo5CXHGFjYAqGGEzyHux",
    "5HBLnpw5yqPzcnpYo4NfMbutH3B3W1sqsegwtah2NBPPU2TT",
    "5FbyeY3nGuYdb4FhXtYEDhVqdFJKopko2P3tTbNZWWh1T3XW",
    "5GWoFBuh3cgaA2GDq1urNh6KsKEzu9mhoGvmhnaNikeS7YYs",
    "5HBAY8VVBcdEuU7EWvtdTN9hrDKXcQneea3mL7YzqHjBgs8Q",
    "5E4DVZUBpCWXmBTgZ6wqbqrSEHgUCQRsXSALW6NGFGMjYndv",
    "5Epn64DZ3oPqdpZKroGa9ArFVCBHDWNdrD7aWcMJk4eA6kbM",
    "5FeYEJXzEHwW9jW4igyse9W4PJBab2eWyCN86V2yV5VhYd8Q",
    "5CAeKnKfcxD3CyY7st8yfk6a46DDvruLZECQofbUsFigsi7b",
    "5GjjR4rCctSC14uFB5bZkEq6S4quQnexvGf1RoiLg4Zb2FXE",
    "5EFXBbybeDQGqGLNuf4cizuqtE4DTzH8yUV6Nh6GhAdNicra",
    "5CSa9KZUVUCkcWeWRMykK98V4TUTXBHAtuSqZoi2AHvB4w1P",
    "5F1QRLR65LYGYiVHW43gVxmvBkuicXSJy9TiDXvxYXS8dZG9",
    "5FQzhHZGhCgUGZSt9afRhbtXMX16WSvG6vFMHK1YwhnzrmrT",
    "5Da6Yej8xxY7ehH5xpB34FDWZreFn9ktndGZg6FbmXvJzXAZ",
    "5E5JhsZ4jocJy4awXbSEsm92RYBKRriirSXys7iaSBpkoHvC",
    "5Hawvtm3Jnaps3CuTyc9gToh1DzyqPfbioDriNoDqwWBBNLV",
    "5Hawvtm3Jnaps3CuTyc9gToh1DzyqPfbioDriNoDqwWBBNLV"
]

async def post_agent(
    agent_file: UploadFile = File(...),
    public_key: str = Form(...),
    file_info: str = Form(...),
    signature: str = Form(...),
    name: str = Form(...),
):
    miner_hotkey = file_info.split(":")[0]
    # Check if miner_hotkey is provided
    if not miner_hotkey:
        raise HTTPException(
            status_code=400,
            detail="miner_hotkey is required"
        )
    
    if miner_hotkey in banned_hotkeys:
        raise HTTPException(
            status_code=400,
            detail="Your miner has been banned for attempting to obfuscate code. If this is in error, please contact us on Discord"
        )
    
    existing_agent = await db.get_agent_by_hotkey(miner_hotkey)
    if existing_agent:
        earliest_allowed_time = existing_agent.last_updated + timedelta(seconds=AGENT_RATE_LIMIT_SECONDS)
        if datetime.now() < earliest_allowed_time:
            raise HTTPException(
                status_code=429,
                detail=f"You must wait {AGENT_RATE_LIMIT_SECONDS} seconds before uploading a new agent version"
            )

    latest_version_num = int(file_info.split(":")[-1])
    if existing_agent and latest_version_num != existing_agent.latest_version:
        raise HTTPException(
            status_code=409,
            detail="This upload request has already been processed"
        )

    agent_name = name if not existing_agent else existing_agent.name

    # Check filename
    if agent_file.filename != "agent.py":
        raise HTTPException(
            status_code=400,
            detail="File must be a python file named agent.py"
        )
    
    if existing_agent:
        existing_agent_version = await db.get_latest_agent_version(existing_agent.agent_id)
        evaluations = await db.get_evaluations_by_version_id(existing_agent_version.version_id)
        for evaluation in evaluations:
            if evaluation.status == "running":
                raise HTTPException(
                    status_code=400,
                    detail="An exisiting version of this agent is currently being evaluated. Please wait for it to finish before uploading a new version."
                )
        for evaluation in evaluations:
            if evaluation.status == "waiting":
                evaluation.status = "replaced"
                evaluation.finished_at = datetime.now()
                await db.store_evaluation(evaluation)
    
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

    keypair = Keypair(public_key=bytes.fromhex(public_key), ss58_format=42)
    if not keypair.verify(file_info, bytes.fromhex(signature)):
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Check if hotkey is registered using fiber
    if miner_hotkey not in await get_subnet_hotkeys():
        raise HTTPException(status_code=400, detail=f"Hotkey not registered on subnet")

    # Static code safety checks ---------------------------------------------------
    try:
        AgentCodeChecker(content).run()
    except CheckError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    agent_id = str(uuid.uuid4()) if not existing_agent else existing_agent.agent_id
    version_id = str(uuid.uuid4())
    
    try:
        await s3_manager.upload_file_object(agent_file.file, f"{version_id}/agent.py")
        logger.info(f"Successfully uploaded agent version {version_id} to S3")
    except Exception as e:
        logger.error(f"Failed to upload agent version to S3: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to store agent version in our database. Please try again later."
        )
    
    # Only set name for new agents, not for updates
    
    agent_object = Agent(
        agent_id=agent_id,
        miner_hotkey=existing_agent.miner_hotkey if existing_agent else miner_hotkey,
        name=agent_name,
        latest_version=existing_agent.latest_version + 1 if existing_agent else 0,
        created_at=existing_agent.created_at if existing_agent else datetime.now(),
        last_updated=datetime.now(),
    )
    result = await db.store_agent(agent_object)
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
    result = await db.store_agent_version(agent_version_object)
    if result == 0:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to store agent version in our database. Please try again later."
        )
    
    await WebSocketManager.get_instance().create_new_evaluations(version_id)

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
