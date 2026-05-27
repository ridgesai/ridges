import asyncio

import api.config as config
import utils.logger as logger
from queries.pre_screening_judge import project_next_pre_screening_job_state


async def pre_screening_projector_loop() -> None:
    """Mirror terminal pre-screening job state into platform-owned agent status."""

    logger.info(
        "Starting pre-screening projector loop: "
        f"interval_seconds={config.PRE_SCREENING_PROJECTOR_POLL_INTERVAL_SECONDS}"
    )

    while True:
        try:
            projected = 0
            while await project_next_pre_screening_job_state():
                projected += 1

            if projected:
                logger.info(f"Projected {projected} pre-screening job state(s)")
        except Exception as exc:
            logger.error(f"Unexpected error in pre-screening projector loop: {type(exc).__name__}: {exc}")

        await asyncio.sleep(config.PRE_SCREENING_PROJECTOR_POLL_INTERVAL_SECONDS)
