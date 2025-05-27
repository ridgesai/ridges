import logging
import os
import sys
import json
from colorama import Back, Fore, Style
from pathlib import Path

class ColoredFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": Fore.CYAN,
        "INFO": Fore.GREEN,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.RED + Back.WHITE,
    }

    def format(self, record):
        levelname = record.levelname
        if levelname in self.COLORS:
            levelname_color = self.COLORS[levelname] + Style.BRIGHT + levelname + Style.RESET_ALL
            record.levelname = levelname_color

        message = super().format(record)

        color = self.COLORS.get(record.levelname, Fore.WHITE)
        message = message.replace("$RESET", Style.RESET_ALL)
        message = message.replace("$BOLD", Style.BRIGHT)
        message = message.replace("$COLOR", color)
        message = message.replace("$BLUE", Fore.BLUE + Style.BRIGHT)

        return message

def get_logs_file():
    return Path(__file__).parents[0] / "logs.json"

def clear_logs():
    """Clear all logs by resetting logs.json to an empty array."""
    log_file = get_logs_file()
    try:
        with open(log_file, 'w') as f:
            json.dump([], f)
    except Exception as e:
        print(f"Error clearing log file: {e}")

def append_log(new_log):
    log_file = get_logs_file()
    try:
        if log_file.exists():
            with open(log_file, 'r') as f:
                logs = json.load(f)
        else:
            logs = []

        log_json = {
            "timestamp": new_log.asctime,
            "milliseconds": new_log.msecs,
            "levelname": new_log.levelname,
            "filename": new_log.name,
            "pathname": new_log.pathname,
            "funcName": new_log.funcName,
            "lineno": new_log.lineno,
            "message": new_log.message,
        }
        
        logs.append(log_json)
        
        # Keep only last 1000 logs to prevent file from growing too large
        logs = logs[-1000:]
        
        with open(log_file, 'w') as f:
            json.dump(logs, f)
    except Exception as e:
        print(f"Error writing to log file: {e}")

class FileHandler(logging.Handler):
    def emit(self, record):
        append_log(record)

def get_logger(name: str):
    logger = logging.getLogger(name.split(".")[-1])
    mode: str = os.getenv("ENV", "prod").lower()

    logger.setLevel(logging.DEBUG if mode != "prod" else logging.INFO)
    logger.handlers.clear()

    format_string = (
        "$BLUE%(asctime)s.%(msecs)03d$RESET | "
        "$COLOR$BOLD%(levelname)-8s$RESET | "
        "$BLUE%(name)s$RESET:"
        "$BLUE%(funcName)s$RESET:"
        "$BLUE%(lineno)d$RESET - "
        "$COLOR$BOLD%(message)s$RESET"
    )

    colored_formatter = ColoredFormatter(format_string, datefmt="%Y-%m-%d %H:%M:%S")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(colored_formatter)
    logger.addHandler(console_handler)

    file_handler = FileHandler()
    file_handler.setFormatter(colored_formatter)
    logger.addHandler(file_handler)

    logger.debug(f"Logging mode is {logging.getLevelName(logger.getEffectiveLevel())}")
    return logger
