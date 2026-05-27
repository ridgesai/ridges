import logging

import sentry_sdk

import api.config as config

logger = logging.getLogger(__name__)


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
        # Set traces_sample_rate to 0.75 to capture 75%
        # of transactions for tracing.
        traces_sample_rate=0.75,
        # Set profile_session_sample_rate to 0.75 to profile 75%
        # of profile sessions.
        profile_session_sample_rate=0.75,
        # Set profile_lifecycle to "trace" to automatically
        # run the profiler on when there is an active transaction
        profile_lifecycle="trace",
    )
