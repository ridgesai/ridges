from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import validator.config as config
from api.endpoints.validator_models import ValidatorHeartbeatRequest
from utils.system_metrics import SystemMetrics, collect_system_metrics
from validator.http_utils import post_ridges_platform_sync
from validator.retry_utils import TRANSIENT_HTTP_ERRORS

logger = logging.getLogger("validator")

HEARTBEAT_ENDPOINT = "/validator/heartbeat"
HEARTBEAT_TIMEOUT_SECONDS = 5
RETRY_BASE_DELAY_SECONDS = 2.0
RETRY_MAX_DELAY_SECONDS = 10.0


def _default_post(session_id: UUID, metrics: SystemMetrics) -> Any:
    return post_ridges_platform_sync(
        HEARTBEAT_ENDPOINT,
        ValidatorHeartbeatRequest(system_metrics=metrics),
        bearer_token=session_id,
        quiet=2,
        timeout=HEARTBEAT_TIMEOUT_SECONDS,
    )


def _default_exit(code: int) -> None:
    os._exit(code)


class HeartbeatSender:
    """Sends heartbeats forever on a dedicated thread.

    ``post``, ``collect_metrics``, ``sleep`` and ``exit_process`` are injectable
    for tests; production uses the platform client, psutil, ``time.sleep`` and
    ``os._exit``.
    """

    def __init__(
        self,
        session_id: UUID,
        *,
        post: Callable[[UUID, SystemMetrics], Any] = _default_post,
        collect_metrics: Callable[[], SystemMetrics] = collect_system_metrics,
        sleep: Callable[[float], None] = time.sleep,
        exit_process: Callable[[int], None] = _default_exit,
    ) -> None:
        self._session_id = session_id
        self._post = post
        self._collect_metrics = collect_metrics
        self._sleep = sleep
        self._exit_process = exit_process
        self._metrics = SystemMetrics()
        self._metrics_lock = threading.Lock()
        self._stop = threading.Event()
        self.sender_thread = threading.Thread(target=self.run_forever, name="heartbeat", daemon=True)
        self.metrics_thread = threading.Thread(
            target=self.refresh_metrics_forever, name="heartbeat-metrics", daemon=True
        )

    # -- public -------------------------------------------------------------

    def start(self) -> "HeartbeatSender":
        self.metrics_thread.start()
        self.sender_thread.start()
        return self

    def stop(self) -> None:
        """Ask both threads to finish (tests and graceful shutdown)."""
        self._stop.set()

    @property
    def metrics(self) -> SystemMetrics:
        with self._metrics_lock:
            return self._metrics

    def send_once(self) -> None:
        """One heartbeat with the transient-error retry budget. Raises on final failure."""
        attempts = max(1, int(config.MAX_HEARTBEAT_FAILURES))
        for attempt in range(attempts):
            try:
                self._post(self._session_id, self.metrics)
                return
            except TRANSIENT_HTTP_ERRORS as exc:
                if attempt >= attempts - 1:
                    raise
                delay = min(RETRY_BASE_DELAY_SECONDS * (2**attempt), RETRY_MAX_DELAY_SECONDS)
                logger.warning(
                    f"Heartbeat attempt {attempt + 1}/{attempts} failed ({type(exc).__name__}); retrying in {delay:.0f}s"
                )
                self._sleep(delay)

    def run_forever(self) -> None:
        """Sender loop. Only a failed heartbeat (after retries) ends it, by exiting the process."""
        logger.info("Starting heartbeat thread...")
        while not self._stop.is_set():
            logger.info("Sending heartbeat...")
            try:
                self.send_once()
            except Exception as exc:
                logger.error(f"Heartbeat failed after all retries, exiting: {type(exc).__name__}: {exc}", exc_info=True)
                self._exit_process(1)
                return
            self._sleep(float(config.SEND_HEARTBEAT_INTERVAL_SECONDS))

    def refresh_metrics_forever(self) -> None:
        """Metrics loop. A failing or hanging collector only leaves the snapshot stale."""
        while not self._stop.is_set():
            try:
                snapshot = self._collect_metrics()
                with self._metrics_lock:
                    self._metrics = snapshot
            except Exception as exc:
                logger.warning(f"Heartbeat metrics collection failed: {type(exc).__name__}: {exc}")
            self._sleep(float(config.SEND_HEARTBEAT_INTERVAL_SECONDS))


def start_heartbeat_thread(session_id: UUID) -> HeartbeatSender:
    """Start the heartbeat sender for this session on its own threads."""
    return HeartbeatSender(session_id).start()
