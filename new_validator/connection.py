# All communications between validator and platform 

from typing import Dict, Any, Optional, Callable

from fiber import Keypair
import websockets 
import asyncio 
import httpx

from actions.evals.evals import handle_begin_evaluation
from actions.chain.weights import handle_set_weights
from new_validator.actions.chain.authentication import sign_validator_message
from new_validator.utils.messaging import Authentication, FinishEvaluation, RequestNextEvaluation, StartEvaluation, UpsertEvaluationRun, ValidatorMessage
from new_validator.config import VERSION_COMMIT_HASH
from utils.logging_utils import get_logger

import json

logger = get_logger(__name__)
websocket_url = None
http_url = None

SCREENER_MODE = False

class ConnectionManager:
    hotkey: Keypair
    ws: Optional[websockets.ClientConnection]
    http: Optional[httpx.AsyncClient]
    pass_message_to_validator: Callable[[str, Any], Any]

    _closing_connection: bool = False

    def __init__(self, hotkey: Keypair, pass_message_to_validator: Callable[[str, Any], Any]) -> None:
        self.hotkey = hotkey
        self.pass_message_to_validator = pass_message_to_validator
        self.create_connections()
    
    async def create_connections(self):
        while True: 
            try: 
                if websocket_url is None:
                    raise RuntimeError("Websocket URL is not configured")
                
                async with websockets.connect(websocket_url, ping_timeout=None, max_size=32 * 1024 * 1024) as ws:
                    self.ws = ws
                    self._closing_connection = False

                    # Authenticate
                    await self.send(Authentication(
                        validator_hotkey=self.hotkey.public_key,
                        version_commit_hash=VERSION_COMMIT_HASH
                    ))

                    # Keep connection alive
                    # TODO: raise SystemExit(f"FATAL: TODO") handlshake
                    
                    try:
                        while True: 
                            message = await ws.recv()
                            await self.pass_message_to_validator(message)

                    except websockets.ConnectionClosed:
                        logger.info("Connection closed - handling disconnect")

                    except Exception as e:
                        logger.error(f"Error in message handling: {e}")
                    
                    finally:
                        await self.close_connections()
                    

            except SystemExit:
                # Authentication failed, don't reconnect
                raise

            except Exception as e:
                logger.error(f"Error connecting to websocket: {e}")

    async def close_connections(self):
        """Properly shutdown the WebsocketApp by cancelling tasks and closing connections."""
        pass
    
    async def send(self, message: ValidatorMessage):
        # Inject hotkey, version commit to each message
        message.validator_hotkey = self.hotkey.public_key
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

            return self._send_ws(
                message=message.model_dump()
            )
        
        return self._send_ws(
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
            await self.ws.send(json.dumps(message))

        except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedError) as e:
            # Connection closed while sending, shutting down
            if not self._closing_connection:
                asyncio.create_task(self.close_connections())
        
        except Exception as e:
            logger.exception(f"Error while sending message – {e}")