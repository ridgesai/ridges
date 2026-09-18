import logging

import pytest

from utils.logger import setup_logging


@pytest.mark.parametrize("level", [logging.NOTSET, logging.WARNING])
def test_debug_mode_leaves_unlisted_third_party_loggers_alone(monkeypatch, level) -> None:
    """DEBUG=true must not pin every logger in the process to DEBUG.

    bittensor's enable_debug() does exactly that, and setup_logging() only resets the loggers it
    lists, so botocore, httpx, asyncio and the rest would flood the log.
    """
    third_party = logging.getLogger("some.third.party.library")
    third_party.setLevel(level)
    monkeypatch.setenv("DEBUG", "true")
    try:
        for _ in range(2):
            setup_logging()
            assert third_party.level == level
            assert logging.getLogger("bittensor").level == logging.DEBUG
    finally:
        monkeypatch.delenv("DEBUG", raising=False)
        setup_logging()
