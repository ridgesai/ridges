"""Long-running background loops the validator/screener runs alongside its main
evaluation loop: heartbeat, weight-setting, and local-storage cleanup.

These are started with `asyncio.create_task` from `validator.main`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import traceback
from collections.abc import Set
from uuid import UUID

import validator.config as config
from api.endpoints.validator_models import ValidatorHeartbeatRequest
from ridges_harbor.shared import DEFAULT_RESULTS_DIR
from utils.cleanup import prune_dirs_older_than
from utils.system_metrics import get_system_metrics
from utils.task_cache import prune_task_cache
from validator.http_utils import get_ridges_platform, post_ridges_platform
from validator.retry_utils import retry_with_backoff
from validator.set_weights import set_weights_from_mapping

logger = logging.getLogger("validator")


# A loop that sends periodic heartbeats to the Ridges platform
async def send_heartbeat_loop(session_id: UUID):
    logger.info("Starting send heartbeat loop...")
    try:
        while True:
            logger.info("Sending heartbeat...")
            system_metrics = await get_system_metrics()
            await retry_with_backoff(
                lambda: post_ridges_platform(
                    "/validator/heartbeat",
                    ValidatorHeartbeatRequest(system_metrics=system_metrics),
                    bearer_token=session_id,
                    quiet=2,
                    timeout=5,
                ),
                max_attempts=config.MAX_HEARTBEAT_FAILURES,
            )
            await asyncio.sleep(config.SEND_HEARTBEAT_INTERVAL_SECONDS)
    except Exception as e:
        logger.error(f"Heartbeat failed after all retries, exiting: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        os._exit(1)


# A loop that periodically sets weights
async def set_weights_loop():
    logger.info("Starting set weights loop...")
    while True:
        weights_mapping = await retry_with_backoff(
            lambda: get_ridges_platform("/scoring/weights", quiet=1),
        )

        try:
            await asyncio.wait_for(
                set_weights_from_mapping(weights_mapping), timeout=config.SET_WEIGHTS_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as e:
            logger.error(f"asyncio.TimeoutError in set_weights_from_mapping(): {e}")

        await asyncio.sleep(config.SET_WEIGHTS_INTERVAL_SECONDS)


# A low-priority background loop that prunes the task cache and Harbor job
# artifacts by age. Fail-safe: unlike the heartbeat loop it must NEVER exit the
# validator — every sweep is wrapped so errors are logged and the loop continues.
#
# ``active_job_dir_names`` / ``active_task_digests`` are the live guard sets owned
# and mutated by ``validator.main``; the loop only reads (snapshots) them so it
# never deletes a directory an in-flight evaluation run is still using.
async def cleanup_loop(active_job_dir_names: Set[str], active_task_digests: Set[str]):
    logger.info("Starting cleanup loop...")
    task_cache_max_age = config.CLEANUP_TASK_CACHE_RETENTION_HOURS * 3600
    artifact_max_age = config.CLEANUP_ARTIFACT_RETENTION_HOURS * 3600
    results_dir = pathlib.Path(config.RIDGES_HARBOR_RESULTS_DIR or DEFAULT_RESULTS_DIR).expanduser().resolve()

    while True:
        try:
            # Snapshot the active-run guards (copies, not the live sets) so the
            # blocking prune running in a worker thread can't observe a mutation.
            job_guard = set(active_job_dir_names)
            task_guard = {digest.replace(":", "_") for digest in active_task_digests}

            # rmtree is blocking; run off the event loop like prune_docker_disk_resources.
            tasks_removed = await asyncio.to_thread(
                prune_task_cache, max_age_seconds=task_cache_max_age, exclude_names=task_guard
            )
            artifacts_removed = await asyncio.to_thread(
                prune_dirs_older_than, results_dir, artifact_max_age, exclude_names=job_guard
            )
            if tasks_removed or artifacts_removed:
                logger.info(f"Cleanup pruned {tasks_removed} cached task(s) and {artifacts_removed} job artifact(s)")
        except Exception as e:
            logger.warning(f"Cleanup sweep failed (best-effort): {type(e).__name__}: {e}")

        await asyncio.sleep(config.CLEANUP_INTERVAL_SECONDS)
