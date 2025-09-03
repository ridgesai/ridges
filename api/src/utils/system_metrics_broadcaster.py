"""
System Metrics Broadcasting Service

Handles periodic broadcasting of system metrics to WebSocket clients.
"""

import asyncio
from loggers.logging_utils import get_logger
from api.src.socket.websocket_manager import WebSocketManager

logger = get_logger(__name__)

async def run_system_metrics_broadcast_loop(interval_seconds: int = 30):
    """
    Run system metrics broadcasting loop with specified interval.
    
    Args:
        interval_seconds: How often to broadcast system metrics (default: 30 seconds)
    """
    logger.info(f"Starting system metrics broadcast loop (interval: {interval_seconds}s)")
    
    while True:
        try:
            # Get WebSocket manager instance
            ws_manager = WebSocketManager.get_instance()
            
            # Check if there are any connected non-validator clients to broadcast to
            if len(ws_manager.clients) > 0:
                # Broadcast system metrics to all non-validator clients
                await ws_manager.broadcast_system_metrics_update()
                logger.debug("System metrics broadcast completed")
            else:
                logger.debug("No clients connected, skipping system metrics broadcast")
                
        except Exception as e:
            logger.error(f"Error in system metrics broadcast loop: {e}")
        
        # Wait for next broadcast
        await asyncio.sleep(interval_seconds)

async def broadcast_system_metrics_once():
    """
    Broadcast system metrics once (useful for immediate updates).
    """
    try:
        ws_manager = WebSocketManager.get_instance()
        await ws_manager.broadcast_system_metrics_update()
        logger.info("One-time system metrics broadcast completed")
    except Exception as e:
        logger.error(f"Error in one-time system metrics broadcast: {e}")
