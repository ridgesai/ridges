import asyncio

from new_validator.connection import ConnectionManager 

async def _send_heartbeat(connection_manager: ConnectionManager):
    """Send periodic heartbeat messages with system metrics to the platform."""
    while connection_manager.ws:
        await asyncio.sleep(2.5)
        if connection_manager.ws:
            status = "available"
            if self.evaluation_task is not None and not self.evaluation_task.done() and not self.evaluation_task.cancelled():
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
