"""
Logging configuration for the Ridges platform.

Provides three things:
  - ConsoleFormatter: human-readable coloured output.
  - RidgesLogger: logging.Logger subclass that adds a fatal() method (logs at
    CRITICAL then raises Exception).
  - setup_logging(): configures the root handler and per-namespace levels.
"""

import logging
import os
import time
import traceback

from asgi_correlation_id import CorrelationIdFilter


class ConsoleFormatter(logging.Formatter):
    """Human-readable coloured formatter for local development."""

    _BUILTIN_ATTRS: frozenset[str] = frozenset(
        {
            "args",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",
            "color_message",
        }
    )

    _COLORS: dict[str, str] = {
        "DEBUG": "\033[90m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[31m",
    }
    _RESET = "\033[0m"

    @staticmethod
    def _extra(record: logging.LogRecord) -> dict:
        """Return extra fields added by the caller, excluding stdlib attrs."""
        return {
            k: v
            for k, v in record.__dict__.items()
            if k not in ConsoleFormatter._BUILTIN_ATTRS
            and not k.startswith("_")
            and not (k == "correlation_id" and v == "-")
        }

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        ct = self.converter(record.created)
        t = time.strftime("%Y-%m-%d %H:%M:%S", ct)
        return f"{t}.{record.msecs:03.0f}"

    def format(self, record: logging.LogRecord) -> str:
        color = self._COLORS.get(record.levelname, "")
        ts = self.formatTime(record)
        msg = record.getMessage()
        extra = self._extra(record)
        suffix = (" | " + " ".join(f"{k}={v}" for k, v in extra.items())) if extra else ""
        name_col = record.name[:26].ljust(26)
        source_col = f"{record.filename}:{record.lineno}"[:20].ljust(20)
        level_col = f"[{color}{record.levelname}{self._RESET}]"
        line = f"{ts}  {name_col}  {source_col}  {level_col} {msg}{suffix}"
        if record.exc_info:
            line += "\n" + "".join(traceback.format_exception(*record.exc_info))
        return line


class RidgesLogger(logging.Logger):
    def fatal(self, message: str) -> None:
        self.critical(message)
        raise Exception(message)


# Must be called at import time so every subsequent logging.getLogger() call
# returns a RidgesLogger instance with .fatal() available.
logging.setLoggerClass(RidgesLogger)


def setup_logging() -> None:
    """Configure logging for the application.

    - Root logger: WARNING (suppresses noisy third-party output by default).
    - Named app namespaces (api, execution, inference_gateway, queries, utils,
      validator, …): DEBUG.
    - Selected third-party loggers: INFO/WARNING as appropriate.

    Safe to call multiple times (clears and rebuilds handlers each time).
    """
    level = logging.DEBUG if os.getenv("DEBUG", "false").lower() == "true" else logging.INFO
    handler = logging.StreamHandler()
    handler.setFormatter(ConsoleFormatter())
    handler.setLevel(level)
    handler.addFilter(CorrelationIdFilter(uuid_length=32, default_value="-"))

    logging.root.handlers.clear()
    logging.root.addHandler(handler)
    logging.root.setLevel(logging.WARNING)

    logger_config = {
        # Application namespaces
        "api": level,
        "db": level,
        "execution": level,
        "inference_gateway": level,
        "miners": level,
        "models": level,
        "queries": level,
        "utils": level,
        "utils.database": level,
        "utils.s3": level,
        "utils.ttl": level,
        "utils.cleanup": level,
        "validator": level,
        # Third-party loggers
        "alembic.runtime.migration": logging.INFO,
        "uvicorn": logging.INFO,
        "uvicorn.error": logging.INFO,
        "uvicorn.access": logging.WARNING,
    }

    for name, level in logger_config.items():
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = False
        lg.handlers.clear()
        lg.addHandler(handler)
