"""WebsocketApp class for managing websocket connections with queue-based message sending."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import websockets
import websockets.exceptions
from ddtrace import tracer

from validator.utils.get_screener_info import get_screener_info
from loggers.logging_utils import get_logger
from validator.config import RIDGES_API_URL, SCREENER_MODE
from validator.socket.handle_message import handle_message
from validator.utils.get_validator_version_info import get_validator_info
from validator.utils.system_metrics import get_system_metrics


websocket_url = RIDGES_API_URL.replace("http", "ws", 1) + "/ws" if RIDGES_API_URL else None

logger = get_logger(__name__)

logger.info(f"SCREENER_MODE: {SCREENER_MODE}")

class WebsocketApp:
    ws: Optional[websockets.ClientConnection]
    evaluation_task: Optional[asyncio.Task]
    sandbox_manager: Optional[Any]
    heartbeat_task: Optional[asyncio.Task]
    _shutting_down: bool
    status_running: bool
    
    def __init__(self):
        self.ws = None
        self.evaluation_task = None
        self.sandbox_manager = None
        self.heartbeat_task = None
        self._shutting_down = False
        self.status_running = False

    @tracer.wrap(resource="send-websocket-message")
    async def send(self, message: Dict[str, Any]):
        if self.ws is None or self._shutting_down:
            logger.error("Websocket not connected")
            return
        
        # Check if websocket is still open before attempting to send
        if hasattr(self.ws, 'closed') and self.ws.closed:
            logger.error("Websocket connection is closed")
            if not self._shutting_down:
                # Trigger shutdown if we detect a closed connection
                asyncio.create_task(self.shutdown())
            return
        
        try:
            logger.debug(f"Sending message: {message.get('event', str(message))}")
            await self.ws.send(json.dumps(message))
            logger.info(f"Message sent: {message.get('event')}")
        except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedError) as e:
            logger.warning(f"Connection closed while sending - triggering shutdown: {e}")
            if not self._shutting_down:
                asyncio.create_task(self.shutdown())
        except Exception as e:
            logger.exception(f"Error while sending message – {e}")

    @tracer.wrap(resource="cancel-evaluation")
    async def cancel_running_evaluation(self):
        """Cancel the currently running evaluation if any."""
        # Force cancel all sandbox tasks immediately
        if self.sandbox_manager:
            logger.info("Force cancelling all sandbox tasks due to websocket disconnect")
            try:
                cleanup_task = asyncio.create_task(self._cleanup_with_timeout())
                await asyncio.wait_for(cleanup_task, timeout=30.0)  # 30 second timeout
            except asyncio.TimeoutError:
                logger.critical("Cleanup timeout - containers may still be running")
            except Exception as e:
                logger.error(f"Error during cleanup: {e}")
            finally:
                self.sandbox_manager = None
            
        if self.evaluation_task and not self.evaluation_task.done():
            logger.info("Cancelling running evaluation due to websocket disconnect")
            task = self.evaluation_task
            self.evaluation_task = None  # Clear reference immediately to prevent double cancellation
            
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=10.0)  # 10 second timeout
            except asyncio.TimeoutError:
                logger.critical("Evaluation task cancellation timeout!")
            except asyncio.CancelledError:
                logger.info("Evaluation cancelled successfully")
            except Exception as e:
                logger.error(f"Error while cancelling evaluation: {e}")
    
    async def _cleanup_with_timeout(self):
        try:
            self.sandbox_manager.force_cancel_all_tasks()
        except Exception as e:
            logger.error(f"Error in force_cancel_all_tasks: {e}")

    async def _send_heartbeat(self):
        """Send periodic heartbeat messages with system metrics to the platform."""

        while self.ws:
            await asyncio.sleep(2.5)
            if self.ws:
                status = "available"
                if self.status_running:
                    status = "screening" if SCREENER_MODE else "evaluating"
                

                # Collect system metrics
                try:
                    logger.debug("Collecting system metrics...")
                    system_metrics = await get_system_metrics()
                    logger.debug(f"Raw system metrics collected: {system_metrics}")
                    
                    # Only include metrics that aren't None
                    metrics_to_send = {k: v for k, v in system_metrics.items() if v is not None}
                    logger.debug(f"Non-null metrics to send: {metrics_to_send}")
                    
                    # Build heartbeat message
                    heartbeat_msg = {"event": "heartbeat", "status": status}
                    if metrics_to_send:
                        heartbeat_msg.update(metrics_to_send)
                        logger.info(f"📊 Sending heartbeat WITH metrics: {metrics_to_send}")
                    else:
                        logger.warning("📊 Sending heartbeat WITHOUT metrics (all None or psutil unavailable)")
                    
                    await self.send(heartbeat_msg)
                    
                except Exception as e:
                    logger.error(f"❌ Failed to collect system metrics, sending heartbeat without them: {e}")
                    # Fallback to heartbeat without metrics
                    await self.send({"event": "heartbeat", "status": status})

    @tracer.wrap(resource="shutdown-websocket-app")
    async def shutdown(self):
        """Properly shutdown the WebsocketApp by cancelling tasks and closing connections."""
        if self._shutting_down:
            return
            
        self._shutting_down = True
        logger.info("Shutting down WebsocketApp...")
        
        try:
            await asyncio.wait_for(self.cancel_running_evaluation(), timeout=45.0)  # 45 second total timeout
        except asyncio.TimeoutError:
            logger.critical("Shutdown timeout - forcing emergency cleanup")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        
        # Close websocket connection
        if self.ws:
            try:
                await asyncio.wait_for(self.ws.close(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Websocket close timeout")
            except Exception as e:
                logger.error(f"Error closing websocket: {e}")
            finally:
                self.ws = None
        
        # Clean up shared HTTP client
        from validator.utils.http_client import cleanup_shared_client
        await cleanup_shared_client()
            
        logger.info("WebsocketApp shutdown complete")

    @tracer.wrap(resource="start-websocket-app")
    async def start(self):
        while True:
            try:
                if websocket_url is None:
                    raise RuntimeError("WebSocket URL is not configured")
                logger.info(f"Connecting to websocket: {websocket_url}")
                async with websockets.connect(websocket_url, ping_timeout=None, max_size=32 * 1024 * 1024) as ws:
                    self.ws = ws
                    self._shutting_down = False  # Reset shutdown flag on new connection
                    logger.info(f"Connected to websocket: {websocket_url}")
                    await self.send(get_screener_info() if SCREENER_MODE else get_validator_info())
                    
                    # Start heartbeat task
                    self.heartbeat_task = asyncio.create_task(self._send_heartbeat())
                    
                    try:
                        while True:
                            message = await ws.recv()
                            await handle_message(self, message)
                    except websockets.ConnectionClosed:
                        logger.info("Connection closed - handling disconnect")
                    except Exception as e:
                        logger.error(f"Error in message handling: {e}")
                    finally:
                        await self.shutdown()
                        
            except SystemExit:
                # Authentication failed, don't reconnect
                raise
            except Exception as e:
                logger.error(f"Error connecting to websocket: {e}")
                
            await asyncio.sleep(5)  # Wait before reconnecting
