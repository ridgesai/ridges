import inspect
import logging
import os
from datetime import datetime

# We want some loggers from third-party libraries to be quieter
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("chain_utils").setLevel(logging.WARNING)


LEVEL_NAME_TO_COLOR = {
    "DEBUG": "\033[90m",  # Gray
    "INFO": "\033[32m",  # Green
    "WARNING": "\033[33m",  # Yellow
    "ERROR": "\033[31m",  # Red
    "FATAL": "\033[31m",  # Red
}

GRAY = "\033[90m"
RESET = "\033[0m"


def print_log(
    level: str,
    message: str,
    *,
    _file: str | None = None,
    _line: int | None = None,
):
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    ms = now.microsecond // 1000

    if _file is None:
        # BUG: This is currently pointing to the logging internal code instead of the caller's code. We should fix this to point to the caller's code.
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        _file = (
            caller.f_code.co_filename.split("/")[-1]
            if caller is not None
            else "?"
        )
        _line = caller.f_lineno if caller is not None else 0

    print(
        f"{timestamp}.{ms:03d} - {_file}:{_line} - [{LEVEL_NAME_TO_COLOR[level]}{level}{RESET}] - {message}"
    )


class RidgesLogHandler(logging.Handler):
    _LEVEL_MAP = {"CRITICAL": "FATAL"}

    def emit(self, record: logging.LogRecord):
        level = self._LEVEL_MAP.get(record.levelname, record.levelname)
        if level not in LEVEL_NAME_TO_COLOR:
            level = "INFO"
        print_log(
            level,
            record.getMessage(),
            _file=record.filename,
            _line=record.lineno,
        )


def debug(message: str):
    if os.getenv("DEBUG", "false").lower() == "true":
        print_log("DEBUG", GRAY + message + RESET)


def info(message: str):
    print_log("INFO", message)


def warning(message: str):
    print_log("WARNING", message)


def error(message: str):
    print_log("ERROR", message)


def fatal(message: str):
    print_log("FATAL", message)
    raise Exception(message)
