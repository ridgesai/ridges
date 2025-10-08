from typing import Dict, Any
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from api.src.models.screener import Screener
from api.src.models.validator import Validator
from loggers.logging_utils import get_logger
from api.src.backend.entities import Client
from api.src.backend.queries.evaluations import does_validator_have_running_evaluation, get_running_evaluation_by_validator_hotkey, get_agent_name_from_version_id, get_miner_hotkey_from_version_id
from api.src.backend.queries.evaluation_runs import all_runs_finished
from api.src.utils.slack import send_slack_message

logger = get_logger(__name__)

async def handle_heartbeat(
    websocket: WebSocket,
    clients: Dict[WebSocket, Client],
    response_json: Dict[str, Any]
):
    """
    Handle heartbeat message from a validator or screener.
    Severs the connection if the client is incorrect
    If client is available, call connect to check and assign evaluations
    Also processes system metrics sent with the heartbeat.
    """
    client: Validator | Screener = clients[websocket]

    alleged_status = response_json["status"] # What the client thinks it's doing
    if alleged_status != client.status:
        client.status_mismatch_count += 1
        logger.warning(f"Client {client.hotkey} status mismatch #{client.status_mismatch_count}: Client says {alleged_status}, but Platform says {client.status}")
        
        if client.status_mismatch_count >= 25:
            logger.error(f"Client {client.hotkey} has {client.status_mismatch_count} consecutive status mismatches. Forcing reconnection.")
            await websocket.send_json({"event": "error", "error": f"Too many status mismatches ({client.status_mismatch_count}). Reconnecting..."})
            await websocket.close()
            raise WebSocketDisconnect()
        else:
            await websocket.send_json({"event": "error", "error": f"Client status mismatch: Client says {alleged_status}, but Platform says {client.status}"})
    else:
        # Reset counter on successful status match
        client.status_mismatch_count = 0

    # Process system metrics if included in heartbeat
    if any(key in response_json for key in ["cpu_percent", "ram_percent", "disk_percent", "containers", "ram_total_gb", "disk_total_gb"]):
        try:
            cpu_percent = response_json.get("cpu_percent")
            ram_percent = response_json.get("ram_percent")
            ram_total_gb = response_json.get("ram_total_gb")
            disk_percent = response_json.get("disk_percent")
            disk_total_gb = response_json.get("disk_total_gb")
            containers = response_json.get("containers")
            
            client.update_system_metrics(cpu_percent, ram_percent, disk_percent, containers, ram_total_gb, disk_total_gb)
            logger.info(f"✅ Updated system metrics for {client.get_type()} {client.hotkey}: CPU={cpu_percent}%, RAM={ram_percent}% ({ram_total_gb}GB), Disk={disk_percent}% ({disk_total_gb}GB), Containers={containers}")
            
        except Exception as e:
            logger.warning(f"❌ Failed to update system metrics for {client.hotkey}: {e}")
    else:
        logger.debug(f"No system metrics in heartbeat from {client.hotkey} (old validator version)")

    if client.status == "available":
        await client.connect()

    if alleged_status == "available" and client.status == "reserving":
        await client.connect()



    # Perform sanity checks regarding the validator/screener's state, and send a Slack message if a sanity check fails
    has_running_evaluation = await does_validator_have_running_evaluation(client.hotkey)
    if (client.status == "screening" or client.status == "evaluating") and has_running_evaluation == False:
        await send_slack_message(f"Client {client.hotkey} is supposedly {client.status}, but has no running evaluation")
    elif client.status == "available" and has_running_evaluation == True:
        await send_slack_message(f"Client {client.hotkey} is supposedly available, but has a running evaluation")