"""WebsocketApp class for managing websocket connections with queue-based message sending."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import websockets

from validator.utils.logging import get_logger
from validator.config import RIDGES_API_URL
from validator.socket.handle_message import handle_message
from validator.utils.get_validator_version_info import get_validator_info

websocket_url = RIDGES_API_URL.replace("http", "ws", 1) + "/ws"

logger = get_logger(__name__)

class WebsocketApp:
    ws: Optional[websockets.ClientConnection] = None
    evaluation_running: asyncio.Event
    evaluation_task: Optional[asyncio.Task] = None
    authentication_failed: bool = False
    
    def __init__(self):
        self.evaluation_running = asyncio.Event()
        self.evaluation_task = None
        self.authentication_failed = False

    async def send(self, message: Dict[str, Any]):
        if self.ws is None:
            logger.error("Websocket not connected")
            await self.cancel_evaluation()
            return
        
        try:
            logger.debug(f"Sending message: {message.get('event', str(message))}")
            await self.ws.send(json.dumps(message))
            logger.info(f"Message sent: {message.get('event')}")
        except Exception as e:
            logger.exception(f"Error while sending message – {e}")

            if self.ws:
                await self.ws.close()
                self.ws = None

    async def cancel_evaluation(self):
        """Cancel the currently running evaluation if any."""
        if self.evaluation_task and not self.evaluation_task.done():
            logger.info("Cancelling running evaluation due to websocket disconnect")
            self.evaluation_task.cancel()
            try:
                await self.evaluation_task
            except asyncio.CancelledError:
                logger.info("Evaluation cancelled successfully")
            except Exception as e:
                logger.error(f"Error while cancelling evaluation: {e}")
        
        # Clear the evaluation running flag
        if self.evaluation_running.is_set():
            self.evaluation_running.clear()
            logger.info("Cleared evaluation_running flag")

    async def _handle_disconnect(self):
        """Handle websocket disconnection by cancelling running evaluation."""
        await self.cancel_evaluation()

    async def shutdown(self):
        """Properly shutdown the WebsocketApp by cancelling tasks and closing connections."""
        logger.info("Shutting down WebsocketApp...")
        
        # Cancel any running evaluation
        await self.cancel_evaluation()
        
        # Close websocket connection
        if self.ws:
            await self.ws.close()
            self.ws = None
            
        logger.info("WebsocketApp shutdown complete")

    async def start(self):
        while True:
            try:
                async with websockets.connect(websocket_url, ping_timeout=None) as ws:
                    self.ws = ws
                    logger.info(f"Connected to websocket: {websocket_url}")
                    await self.send(get_validator_info())
                    
                    try:
                        while True:
                            message = await ws.recv()
                            await handle_message(self, message)
                    except websockets.ConnectionClosed:
                        logger.info("Connection closed - handling disconnect")
                        await self._handle_disconnect()
                    except SystemExit as e:
                        # Authentication failed, don't reconnect
                        logger.error(f"Authentication failed: {e}")
                        raise
                    except Exception as e:
                        logger.error(f"Error in message handling: {e}")
                        await self._handle_disconnect()
                    finally:
                        self.ws = None
                        
            except SystemExit:
                # Authentication failed, don't reconnect
                raise
            except Exception as e:
                logger.exception(f"Error connecting to websocket: {e}")
                await self._handle_disconnect()
                
            # Check if authentication failed before reconnecting
            if self.authentication_failed:
                logger.error("Authentication failed. Validator must be registered in the metagraph.")
                raise SystemExit("FATAL: You must be a registered validator in the metagraph to connect")
                
            await asyncio.sleep(5)  # Wait before reconnecting
