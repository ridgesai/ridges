"""
Handle system metrics updates from validators and screeners
"""

from typing import Dict, Any
from fastapi import WebSocket

from loggers.logging_utils import get_logger
from api.src.backend.entities import Client

logger = get_logger(__name__)

async def handle_system_metrics(
    client: Client,
    response_json: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle system-metrics message from a validator or screener"""
    
    try:
        # Extract system metrics from the message
        cpu_percent = response_json.get("cpu_percent")
        ram_percent = response_json.get("ram_percent") 
        ram_total_gb = response_json.get("ram_total_gb")
        disk_percent = response_json.get("disk_percent")
        disk_total_gb = response_json.get("disk_total_gb")
        containers = response_json.get("containers")
        
        # Validate the metrics (should be numbers or None)
        if cpu_percent is not None and not isinstance(cpu_percent, (int, float)):
            logger.warning(f"Invalid cpu_percent from {client.hotkey}: {cpu_percent}")
            cpu_percent = None
        if ram_percent is not None and not isinstance(ram_percent, (int, float)):
            logger.warning(f"Invalid ram_percent from {client.hotkey}: {ram_percent}")
            ram_percent = None
        if ram_total_gb is not None and not isinstance(ram_total_gb, (int, float)):
            logger.warning(f"Invalid ram_total_gb from {client.hotkey}: {ram_total_gb}")
            ram_total_gb = None
        if disk_percent is not None and not isinstance(disk_percent, (int, float)):
            logger.warning(f"Invalid disk_percent from {client.hotkey}: {disk_percent}")
            disk_percent = None
        if disk_total_gb is not None and not isinstance(disk_total_gb, (int, float)):
            logger.warning(f"Invalid disk_total_gb from {client.hotkey}: {disk_total_gb}")
            disk_total_gb = None
        if containers is not None and not isinstance(containers, int):
            logger.warning(f"Invalid containers from {client.hotkey}: {containers}")
            containers = None
            
        # Update the client's system metrics
        client.update_system_metrics(
            cpu_percent=float(cpu_percent) if cpu_percent is not None else None,
            ram_percent=float(ram_percent) if ram_percent is not None else None,
            disk_percent=float(disk_percent) if disk_percent is not None else None,
            containers=int(containers) if containers is not None else None,
            ram_total_gb=float(ram_total_gb) if ram_total_gb is not None else None,
            disk_total_gb=float(disk_total_gb) if disk_total_gb is not None else None
        )
        
        logger.debug(f"Updated system metrics for {client.get_type()} {client.hotkey}")
        
        return {"status": "success", "message": "System metrics updated"}
        
    except Exception as e:
        logger.error(f"Error handling system metrics from {client.hotkey}: {e}")
        return {"status": "error", "message": f"Failed to update system metrics: {str(e)}"}
