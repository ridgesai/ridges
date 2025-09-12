# All communications between validator and platform 

from typing import Dict, Any

import websockets 
from typing import Any, Optional

# TODO: Common message protocol structure?

class SocketManager:
    ws: Optional[websockets.ClientConnection]

    def __init__(self) -> None:
        self.ws = None

    async def create_connection(self):
        pass

    async def close_connection(self):
        pass
    
    async def send(self, message: Dict[str, Any]): 
        pass

    async def handle_message():
        event = ""

        match event:
            case "evaluation":
                pass

            case "set-weights":
                pass

            case "authentication-failed":
                raise SystemExit(f"FATAL: TODO")
        # Heartbeat
        # Start eval
        # Post results for eval run
        # Weights
        pass

class SocketManager():
    pass

    async def send():
        pass