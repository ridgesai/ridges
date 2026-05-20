import datetime
import logging
import os
import traceback

from asgi_correlation_id import CorrelationIdFilter

_BUILTIN_LOG_RECORD_ATTRS = frozenset(
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
    }
)

LEVEL_NAME_TO_COLOR = {
    "DEBUG": "\033[90m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[31m",
}
RESET = "\033[0m"


class RidgesLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = record.levelname
            color = LEVEL_NAME_TO_COLOR.get(level, "")

            extra = {
                k: v for k, v in record.__dict__.items() if k not in _BUILTIN_LOG_RECORD_ATTRS and not k.startswith("_")
            }

            ts_dt = datetime.datetime.fromtimestamp(record.created)
            ts = ts_dt.strftime("%Y-%m-%d %H:%M:%S") + f".{ts_dt.microsecond // 1000:03d}"

            msg = record.getMessage()
            suffix = (" | " + " ".join(f"{k}={v}" for k, v in extra.items())) if extra else ""

            print(f"{ts} - {record.filename}:{record.lineno} - [{color}{level}{RESET}] - {msg}{suffix}")

            if record.exc_info:
                traceback.print_exception(*record.exc_info)

        except Exception:
            self.handleError(record)


def setup_logging() -> None:
    """Configure the root logger with RidgesLogHandler. Call once at startup."""
    debug_mode = os.getenv("DEBUG", "false").lower() == "true"
    level = logging.DEBUG if debug_mode else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.filters.clear()
    root.addHandler(RidgesLogHandler())
    root.addFilter(CorrelationIdFilter(default_value="-"))

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("chain_utils").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def fatal(message: str) -> None:
    """Log at CRITICAL level and exit the process."""
    logging.getLogger("ridges").critical(message)
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Backward-compat shims for modules not yet migrated to stdlib logging.
# These route through a named logger so filename:lineno is still correct.
# TODO: remove once all callers use logging.getLogger(__name__) directly.
# ---------------------------------------------------------------------------
_compat_logger = logging.getLogger("ridges.compat")


def debug(message: str) -> None:
    if os.getenv("DEBUG", "false").lower() == "true":
        _compat_logger.debug(message, stacklevel=2)


def info(message: str) -> None:
    _compat_logger.info(message, stacklevel=2)


def warning(message: str) -> None:
    _compat_logger.warning(message, stacklevel=2)


def error(message: str) -> None:
    _compat_logger.error(message, stacklevel=2)
