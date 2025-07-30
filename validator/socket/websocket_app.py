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


websocket_url = RIDGES_API_URL.replace("http", "ws", 1) + "/ws"

logger = get_logger(__name__)

logger.info(f"SCREENER_MODE: {SCREENER_MODE}")

class WebsocketApp:
    ws: Optional[websockets.ClientConnection]
    evaluation_task: Optional[asyncio.Task]
    sandbox_manager: Optional[Any]
    _shutting_down: bool
    
    def __init__(self):
        self.ws = None
        self.evaluation_task = None
        self.sandbox_manager = None
        self._shutting_down = False

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
            # Don't log evaluation-run-log messages, too noisy
            if message.get('event') != 'evaluation-run-log':
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
            self.sandbox_manager.force_cancel_all_tasks()
            self.sandbox_manager = None
            
        if self.evaluation_task and not self.evaluation_task.done():
            logger.info("Cancelling running evaluation due to websocket disconnect")
            task = self.evaluation_task
            self.evaluation_task = None  # Clear reference immediately to prevent double cancellation
            
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info("Evaluation cancelled successfully")
            except Exception as e:
                logger.error(f"Error while cancelling evaluation: {e}")

    @tracer.wrap(resource="shutdown-websocket-app")
    async def shutdown(self):
        """Properly shutdown the WebsocketApp by cancelling tasks and closing connections."""
        if self._shutting_down:
            return
            
        self._shutting_down = True
        logger.info("Shutting down WebsocketApp...")
        
        # Cancel any running evaluation
        await self.cancel_running_evaluation()
        
        # Close websocket connection
        if self.ws:
            await self.ws.close()
            self.ws = None
            
        logger.info("WebsocketApp shutdown complete")

    @tracer.wrap(resource="start-websocket-app")
    async def start(self):
        while True:
            try:
                async with websockets.connect(websocket_url, ping_timeout=None) as ws:
                    self.ws = ws
                    self._shutting_down = False  # Reset shutdown flag on new connection
                    logger.info(f"Connected to websocket: {websocket_url}")
                    await self.send(get_screener_info() if SCREENER_MODE else get_validator_info())
                    
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
