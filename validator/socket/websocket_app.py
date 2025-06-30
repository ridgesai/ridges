"""WebsocketApp class for managing websocket connections with queue-based message sending."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import websockets

from validator.utils.logging import get_logger
from validator.config import RIDGES_WS_URL
from validator.socket.handle_message import handle_message
from validator.utils.get_validator_version_info import get_validator_version_info

logger = get_logger(__name__)

class WebsocketApp:
    ws: Optional[websockets.ClientConnection] = None
    evaluation_running: asyncio.Event
    
    def __init__(self):
        self.evaluation_running = asyncio.Event()

    async def send(self, message: Dict[str, Any]):
        if self.ws is None:
            logger.error("Websocket not connected")
            return
        
        try:
            logger.debug(f"Sending message: {message.get('event', str(message))}")
            await self.ws.send(json.dumps(message))
            logger.info(f"Message sent: {message.get('event')}")
        except Exception as e:
            logger.exception(f"Error while sending message – {e}")

    async def start(self):
        while True:
            try:
                async with websockets.connect(RIDGES_WS_URL, ping_timeout=None) as ws:
                    self.ws = ws
                    logger.info(f"Connected to websocket: {RIDGES_WS_URL}")
                    await self.send(get_validator_version_info())
                    while True:
                        try:
                            message = await ws.recv()
                            await handle_message(self, message)
                        except websockets.ConnectionClosed:
                            self.ws = None
                            logger.info(f"Connection closed – reconnecting in 5 seconds")
                            await asyncio.sleep(5)
                            break
            except Exception as e:
                self.ws = None
                logger.exception(f"Error while connecting to websocket – {e}")
                await asyncio.sleep(5)
