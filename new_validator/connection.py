# All communications between validator and platform 

from typing import Dict, Any, Optional

import websockets 
import asyncio 
import httpx

from actions.evals.evals import handle_begin_evaluation
from actions.chain.weights import handle_set_weights
from utils.logging_utils import get_logger

import json

logger = get_logger(__name__)
websocket_url = None
http_url = None

SCREENER_MODE = False

class ConnectionManager:
    ws: Optional[websockets.ClientConnection]
    http: Optional[httpx.AsyncClient]

    _closing_connection: bool = False
    
    async def create_connections(self):
        while True: 
            try: 
                if websocket_url is None:
                    raise RuntimeError("Websocket URL is not configured")
                
                async with websockets.connect(websocket_url, ping_timeout=None, max_size=32 * 1024 * 1024) as ws:
                    self.ws = ws
                    self._closing_connection = False

                    # Authenticate
                    await self.send()

                    # Start heartbeat tasks - every 2.5s, report to platform the status 

                    # Keep connection alive

                    # Create http connection once ws is confirmed
                    self.http = httpx.AsyncClient()

            except SystemExit:
                # Authentication failed, don't reconnect
                raise

            except Exception as e:
                logger.error(f"Error connecting to websocket: {e}")

    async def close_connections(self):
        """Properly shutdown the WebsocketApp by cancelling tasks and closing connections."""
        pass
    
    async def send(self, message: Dict[str, Any]):
        if self.ws is None or self._closing_connection:
            logger.error("Websocket not connected")
            return 
        
        # Check if socket is still open
        if hasattr(self.ws, 'closed') and self.ws.closed:
            logger.error("Websocket connection is closed")
            if not self._closing_connection:
                # Trigger shutdown if we detect a closed connection
                asyncio.create_task(self.close_connections())
            return
        
        try:
            await self.ws.send(json.dumps(message))

        except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedError) as e:
            # Connection closed while sending, shutting down
            if not self._closing_connection:
                asyncio.create_task(self.close_connections())
        
        except Exception as e:
            logger.exception(f"Error while sending message – {e}")

    async def handle_message():
        event = "authentication-failed"

        match event:
            case "begin-evaluation":
                handle_begin_evaluation()

            case "set-weights":
                handle_set_weights()

            case "authentication-failed":
                raise SystemExit(f"FATAL: TODO")
        # Heartbeat
        # Start eval
        # Post results for eval run
        # Weights