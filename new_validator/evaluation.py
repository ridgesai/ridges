from new_validator.resource_management import get_system_metrics
from new_validator.connection import ConnectionManager

import asyncio
from typing import Optional

from logging import getLogger

from shared.messaging import Heartbeat, NewEvaluationInstruction

logger = getLogger(__name__)

class EvaluationManager():
    connection_manager: ConnectionManager
    evaluation_task: Optional[asyncio.Task]
    heartbeat_task: Optional[asyncio.Task]

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self.connection_manager = connection_manager

        # Start heartbeat task
        self.heartbeat_task = asyncio.create_task(self._send_heartbeat())
        
    def run_evaluation(evaluation: NewEvaluationInstruction):
        pass
        
    async def _send_heartbeat(self):
        """Send periodic heartbeat messages with system metrics to the platform."""
        while self.connection_manager.ws:
            await asyncio.sleep(2.5)

            status = "available"

            if self.evaluation_task is not None and not self.evaluation_task.done() and not self.evaluation_task.cancelled():
                status = "evaluating"

            # Collect system metrics
            try:
                logger.debug("Collxecting system metrics...")
                system_metrics = await get_system_metrics()
                logger.debug(f"Raw system metrics collected: {system_metrics}")
                
                # Only include metrics that aren't None
                metrics_to_send = {k: v for k, v in system_metrics.items() if v is not None}
                logger.debug(f"Non-null metrics to send: {metrics_to_send}")
                
                heartbeat = Heartbeat(
                    status=status,
                    **metrics_to_send 
                )

                if metrics_to_send:
                    logger.info(f"📊 Sending heartbeat WITH metrics: {metrics_to_send}")
                else:
                    logger.warning("📊 Sending heartbeat WITHOUT metrics (all None or psutil unavailable)")

                await self.connection_manager.send(message=heartbeat)

            except Exception as e:
                logger.error(f"❌ Failed to collect system metrics, sending heartbeat without them: {e}")
                # Fallback to heartbeat without metrics
                await self.send({"event": "heartbeat", "status": status})


    def handle_evaluation_request():
        pass 

    def shutdown_evaluations():
        pass
    