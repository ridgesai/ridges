import asyncio

import api.config as config
import utils.logger as logger
from queries.approval import project_next_approval_job_state


async def approval_projector_loop() -> None:
    """Mirror completed or human-review approval jobs into platform-owned state tables."""

    logger.info(f"Starting approval projector loop: interval_seconds={config.APPROVAL_PROJECTOR_POLL_INTERVAL_SECONDS}")

    while True:
        try:
            projected = 0
            while await project_next_approval_job_state():
                projected += 1

            if projected:
                logger.info(f"Projected {projected} approval job state(s)")
        except Exception as exc:
            logger.error(f"Unexpected error in approval projector loop: {type(exc).__name__}: {exc}")

        await asyncio.sleep(config.APPROVAL_PROJECTOR_POLL_INTERVAL_SECONDS)
