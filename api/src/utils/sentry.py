import logging
import re
from typing import Any

import sentry_sdk

import api.config as config

logger = logging.getLogger(__name__)

# High-frequency endpoints (frontend and validator polling) that dominate span volume.
# Sampling runs before FastAPI resolves the route, so these match the raw request path.
_HIGH_FREQUENCY_PATHS = frozenset(
    {
        "/competitions",
        "/retrieval/evaluations-for-agent",
        "/retrieval/agent-code",
        "/retrieval/queue",
        "/validator/update-evaluation-run",
        "/validator/check-cancellation",
        "/scaling/pending-work",
        "/upload/eval-pricing/history",
    }
)
_LEADERBOARD_PATTERNS = (re.compile(r"^/evaluation-sets/[^/]+/leaderboard$"),)


def _is_high_frequency_path(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    return normalized in _HIGH_FREQUENCY_PATHS or any(pattern.match(normalized) for pattern in _LEADERBOARD_PATTERNS)


def traces_sampler(sampling_context: dict[str, Any]) -> float:
    """Sample high frequency endpoints at a lower rate than everything else.

    The upstream sampling decision is deliberately ignored so callers cannot push
    span volume past the configured rates.
    """
    asgi_scope = sampling_context.get("asgi_scope") or {}
    path = asgi_scope.get("path")
    if isinstance(path, str) and _is_high_frequency_path(path):
        return config.SENTRY_HIGH_FREQUENCY_TRACES_SAMPLE_RATE
    return config.SENTRY_TRACES_SAMPLE_RATE


def initialize_sentry() -> None:
    """Initialize Sentry SDK"""
    if not config.SENTRY_DSN:
        logger.debug("Sentry DSN not configured, skipping Sentry initialization")
        return

    sentry_sdk.init(
        dsn=config.SENTRY_DSN,
        environment=config.ENV,
        # Add data like request headers and IP for users,
        # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
        send_default_pii=True,
        # Enable sending logs to Sentry
        enable_logs=True,
        # Per-endpoint transaction sampling; error events are not affected.
        traces_sampler=traces_sampler,
    )
