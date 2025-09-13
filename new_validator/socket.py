# All communications between validator and platform 

from typing import Dict, Any

import websockets 
from typing import Any, Optional

from actions.evals import handle_begin_evaluation
from actions.weights import handle_set_weights

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
        event_types: str = "heartbeat" | "post_eval_results"
        pass

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

class SocketManager():
    pass

    async def send():
        pass