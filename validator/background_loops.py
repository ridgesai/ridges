"""Long-running background loops the validator/screener runs alongside its main
evaluation loop: heartbeat, weight-setting, and local-storage cleanup.

These are started with `asyncio.create_task` from `validator.main`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
from collections.abc import Set
from uuid import UUID

import validator.config as config
from api.endpoints.validator_models import ValidatorHeartbeatRequest
from ridges_harbor.shared import DEFAULT_RESULTS_DIR
from utils.cleanup import prune_dirs_older_than
from utils.docker import (
    prune_caches_under_disk_pressure,
    prune_docker_disk_resources,
    sweep_leaked_harbor_images,
    sweep_stale_harbor_containers,
)
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
        logger.error(f"Heartbeat failed after all retries, exiting: {type(e).__name__}: {e}", exc_info=True)
        os._exit(1)


# A loop that periodically sets weights
async def set_weights_loop():
    logger.info("Starting set weights loop...")
    while True:
        try:
            weights_mapping = await retry_with_backoff(
                lambda: get_ridges_platform("/scoring/weights", quiet=1),
            )
            await asyncio.wait_for(
                set_weights_from_mapping(weights_mapping), timeout=config.SET_WEIGHTS_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.error("Timed out while setting weights; retrying on the next interval")
        except Exception as e:
            logger.error(f"Weight-setting tick failed: {type(e).__name__}: {e}")

        await asyncio.sleep(config.SET_WEIGHTS_INTERVAL_SECONDS)


# A low-priority background loop that prunes the task cache and Harbor job
# artifacts by age. Fail-safe: unlike the heartbeat loop it must NEVER exit the
# validator — every sweep is wrapped so errors are logged and the loop continues.
async def cleanup_loop(active_task_digests: Set[str]):
    logger.info("Starting cleanup loop...")
    task_cache_max_age = config.CLEANUP_TASK_CACHE_RETENTION_HOURS * 3600
    artifact_max_age = config.CLEANUP_ARTIFACT_RETENTION_HOURS * 3600
    results_dir = pathlib.Path(config.RIDGES_HARBOR_RESULTS_DIR or DEFAULT_RESULTS_DIR).expanduser().resolve()

    while True:
        try:
            # Snapshot the active-run guard (a copy, not the live set) so the
            # blocking prune running in a worker thread can't observe a mutation.
            task_guard = {digest.replace(":", "_") for digest in active_task_digests}

            # rmtree is blocking; run off the event loop like prune_docker_disk_resources.
            tasks_removed = await asyncio.to_thread(
                prune_task_cache, max_age_seconds=task_cache_max_age, exclude_names=task_guard
            )
            artifacts_removed = await asyncio.to_thread(prune_dirs_older_than, results_dir, artifact_max_age)
            if tasks_removed or artifacts_removed:
                logger.info(f"Cleanup pruned {tasks_removed} cached task(s) and {artifacts_removed} job artifact(s)")
        except Exception as e:
            logger.warning(f"Cleanup sweep failed (best-effort): {type(e).__name__}: {e}")

        if config.CLEANUP_DOCKER_ENABLED and config.RIDGES_ENVIRONMENT_TYPE == "docker":
            dry_run = config.CLEANUP_DOCKER_DRY_RUN
            containers = {"count": 0, "names": [], "errors": 0}
            images = {"count": 0, "names": [], "errors": 0}
            prune = {"image_bytes": 0, "build_bytes": 0, "errors": 0}
            pressure_prune = {"fired": False, "image_bytes": 0, "build_bytes": 0, "errors": 0}
            disk_percent = None
            errors = 0

            try:
                metrics = await get_system_metrics()
                disk_percent = metrics.disk_percent
            except Exception as e:
                errors += 1
                logger.warning(f"Janitor disk metrics failed (best-effort): {type(e).__name__}: {e}")

            try:
                containers = await asyncio.to_thread(
                    sweep_stale_harbor_containers,
                    stopped_grace_sec=config.CLEANUP_STOPPED_GRACE_MINUTES * 60,
                    running_ttl_sec=config.CLEANUP_RUNNING_TTL_HOURS * 3600,
                    dry_run=dry_run,
                )
            except Exception as e:
                errors += 1
                logger.warning(f"Janitor container sweep failed (best-effort): {type(e).__name__}: {e}")

            try:
                images = await asyncio.to_thread(
                    sweep_leaked_harbor_images,
                    tag_grace_sec=config.CLEANUP_IMAGE_TAG_GRACE_HOURS * 3600,
                    dry_run=dry_run,
                    disk_used_percent=disk_percent,
                    pulled_image_pressure_percent=config.CLEANUP_PULLED_IMAGE_DISK_PERCENT,
                )
            except Exception as e:
                errors += 1
                logger.warning(f"Janitor image sweep failed (best-effort): {type(e).__name__}: {e}")

            try:
                prune = await asyncio.to_thread(
                    prune_docker_disk_resources,
                    dry_run=dry_run,
                    until="1h",
                )
            except Exception as e:
                errors += 1
                logger.warning(f"Janitor dangling prune failed (best-effort): {type(e).__name__}: {e}")

            try:
                metrics = await get_system_metrics()
                disk_percent = metrics.disk_percent
                if disk_percent is None:
                    logger.warning("Janitor: skipping pressure prune (disk metrics unavailable)")
                else:
                    pressure_prune = await asyncio.to_thread(
                        prune_caches_under_disk_pressure,
                        disk_used_percent=disk_percent,
                        pressure_percent=config.CLEANUP_DISK_PRESSURE_PERCENT,
                        dry_run=dry_run,
                    )
                    if pressure_prune.get("fired"):
                        logger.info(
                            f"Janitor: pressure prune at {disk_percent:.0f}% disk reclaimed "
                            f"{pressure_prune.get('image_bytes', 0) / 1e6:.0f} MB (images) + "
                            f"{pressure_prune.get('build_bytes', 0) / 1e6:.0f} MB (build cache), "
                            f"errors={pressure_prune.get('errors', 0)} (dry_run={dry_run})"
                        )
            except Exception as e:
                errors += 1
                logger.warning(f"Janitor cache prune failed (best-effort): {type(e).__name__}: {e}")

            errors += sum(
                summary.get("errors", 0)
                for summary in (containers, images, prune, pressure_prune)
            )
            candidate_names = (containers.get("names") or []) + (images.get("names") or [])
            disk_display = f"{disk_percent:.0f}" if disk_percent is not None else "unknown"
            names_display = ",".join(candidate_names[:20]) or "-"
            logger.info(
                f"Janitor: containers={containers.get('count', 0)} "
                f"images={images.get('count', 0)} prune_bytes={prune.get('image_bytes', 0)} "
                f"disk_percent={disk_display} errors={errors} "
                f"dry_run={str(dry_run).lower()} names={names_display}"
            )

        await asyncio.sleep(config.CLEANUP_INTERVAL_SECONDS)
