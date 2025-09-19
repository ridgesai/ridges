# All communications between validator and platform 

from typing import Dict, Any, Optional, Callable

from fiber import Keypair
import websockets 
import asyncio 
import httpx

from new_validator.authentication import sign_validator_message
from shared.messaging import Authentication, FinishEvaluation, RequestNextEvaluation, StartEvaluation, UpsertEvaluationRun, ValidatorMessage
from new_validator.config import VERSION_COMMIT_HASH, WEBSOCKET_URL, RIDGES_API_URL
from utils.logging_utils import get_logger

import json

logger = get_logger(__name__)

class ConnectionManager:
    hotkey: Keypair
    ws: Optional[websockets.ClientConnection]
    http: Optional[httpx.AsyncClient]
    pass_message_to_validator: Callable[[str, Any], Any]

    _closing_connection: bool = False
    _connected_event: asyncio.Event

    def __init__(self, hotkey: Keypair, pass_message_to_validator: Callable[[str, Any], Any]) -> None:
        self.hotkey = hotkey
        self.pass_message_to_validator = pass_message_to_validator
        self.ws = None  # Initialize ws attribute  
        self.http = httpx.AsyncClient(timeout=30.0)  # Initialize HTTP client
        self._connected_event = asyncio.Event()
        # Start connection task
        asyncio.create_task(self.create_connections())

    async def create_connections(self):
        retry_count = 0
        max_retries = 3
        
        while retry_count < max_retries: 
            try: 
                if WEBSOCKET_URL is None or WEBSOCKET_URL == "":
                    raise RuntimeError("Websocket URL is not configured")
                
                logger.info(f"Connecting to websocket (attempt {retry_count + 1}/{max_retries})")
                async with websockets.connect(WEBSOCKET_URL, ping_timeout=None, max_size=32 * 1024 * 1024) as ws:
                    self.ws = ws
                    self._closing_connection = False

                    # Authenticate
                    auth_message = Authentication(signature="placeholder")  # send() will inject proper signature
                    await self.send(auth_message)

                    # Signal that connection is established
                    self._connected_event.set()
                    retry_count = 0  # Reset retry count on successful connection

                    # Keep connection alive
                    
                    try:
                        while True: 
                            message = await ws.recv()
                            await self.pass_message_to_validator(message)

                    except websockets.ConnectionClosed:
                        logger.info("Connection closed - handling disconnect")

                    except Exception as e:
                        logger.error(f"Error in message handling: {e}")
                    
                    finally:
                        self._connected_event.clear()
                        await self.close_connections()
                    

            except SystemExit:
                # Authentication failed, don't reconnect
                raise

            except Exception as e:
                retry_count += 1
                logger.error(f"Error connecting to websocket (attempt {retry_count}/{max_retries}): {e}")
                
                if retry_count >= max_retries:
                    logger.error("Max connection retries exceeded. Shutting down.")
                    raise SystemExit("Failed to connect to platform after multiple attempts")
                
                # Wait longer between retries
                wait_time = retry_count * 10  # 10s, 20s, 30s
                logger.info(f"Waiting {wait_time} seconds before retry...")
                await asyncio.sleep(wait_time)

    async def wait_for_connection(self):
        """Wait until the websocket connection is established."""
        await self._connected_event.wait()

    async def fetch_agent_source_code(self, version_id: str) -> str:
        """Fetch agent source code from the platform API."""
        if not self.http:
            raise RuntimeError("HTTP client not initialized")
        
        try:
            # Construct the API URL for fetching agent source code
            api_url = f"{RIDGES_API_URL}/retrieval/agent-version-file"
            
            # Make the request to fetch agent source code as text
            response = await self.http.get(
                api_url,
                params={
                    "version_id": version_id,
                    "return_as_text": True
                }
            )
            
            response.raise_for_status()  # Raise exception for HTTP errors
            
            agent_source_code = response.text
            logger.info(f"Successfully fetched agent source code for version {version_id} ({len(agent_source_code)} characters)")
            return agent_source_code
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.error(f"Agent version {version_id} not found on platform")
                raise ValueError(f"Agent version {version_id} not found")
            else:
                logger.error(f"HTTP error {e.response.status_code} while fetching agent source code for {version_id}: {e}")
                raise RuntimeError(f"Failed to fetch agent source code: HTTP {e.response.status_code}")
        except Exception as e:
            logger.error(f"Error fetching agent source code for version {version_id}: {e}")
            raise RuntimeError(f"Failed to fetch agent source code: {e}")

    async def close_connections(self):
        """Properly shutdown the WebsocketApp by cancelling tasks and closing connections."""
        if self.http:
            await self.http.aclose()
        pass
    
    async def send(self, message: ValidatorMessage):
        # Inject hotkey, version commit to each message
        message.validator_hotkey = self.hotkey.public_key.hex()  # Convert bytes to hex string
        message.version_commit_hash = VERSION_COMMIT_HASH

        if isinstance(message, (
            Authentication,
            StartEvaluation,
            UpsertEvaluationRun,
            FinishEvaluation
        )):
            signature = sign_validator_message(
                validator_hotkey=self.hotkey,
                payload=message
            )

            message.signature = signature

            return await self._send_ws(
                message=message.model_dump()
            )
        
        return await self._send_ws(
            message=message.model_dump()
        )

    async def _send_http():
        pass

    async def _send_ws(self, message: Dict[str, Any]):
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
            # Handle datetime serialization with default=str
            json_message = json.dumps(message, default=str)
            await self.ws.send(json_message)

        except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedError) as e:
            # Connection closed while sending, shutting down
            if not self._closing_connection:
                asyncio.create_task(self.close_connections())
        
        except Exception as e:
            logger.exception(f"Error while sending message – {e}")