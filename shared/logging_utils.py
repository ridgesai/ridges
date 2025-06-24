import logging
import os
import sys
import json
from colorama import Back, Fore, Style
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

logging_db_path = "logging.db"

# Global set to store active coroutines
active_coroutines = set()

# Global variable to store evaluation loop number
eval_loop_num = 0

def logging_update_active_coroutines(task_name: str, is_running: bool) -> None:
    """Update the status of a task in the global task_statuses dictionary."""
    global active_coroutines
    if is_running:
        active_coroutines.add(task_name)
    else:
        active_coroutines.discard(task_name)

def logging_update_eval_loop_num(loop_number) -> None:
    """Update the evaluation loop number."""
    global eval_loop_num
    eval_loop_num = loop_number

class DatabaseHandler(logging.Handler):
    """A logging handler that writes logs to a database. This is used by Cave."""
    MAX_LOGS = 1000
    
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
        self.db_path = logging_db_path
        # Initialize the database if it doesn't exist
        self._init_db()

    def _init_db(self):
        """Initialize the database with the required table."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id TEXT PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    levelname TEXT NOT NULL,
                    name TEXT NOT NULL,
                    pathname TEXT NOT NULL,
                    funcName TEXT NOT NULL,
                    lineno INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    active_coroutines TEXT NOT NULL,
                    eval_loop_num INTEGER NOT NULL
                )
            """)
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def _maintain_log_limit(self, cursor):
        """Delete oldest logs if we exceed MAX_LOGS."""
        cursor.execute("SELECT COUNT(*) FROM logs")
        count = cursor.fetchone()[0]
        
        if count > self.MAX_LOGS:
            # Delete oldest logs to maintain MAX_LOGS limit
            cursor.execute("""
                DELETE FROM logs 
                WHERE id IN (
                    SELECT id FROM logs 
                    ORDER BY timestamp ASC 
                    LIMIT ?
                )
            """, (count - self.MAX_LOGS,))

    def emit(self, record):
        """Post a log record to the database."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            try:
                # Ensure all values are of the correct type
                values = (
                    str(uuid.uuid4()),
                    datetime.now().isoformat(),
                    str(record.levelname),
                    str(record.name),
                    str(Path(record.pathname).relative_to(Path.cwd())),
                    str(record.funcName),
                    int(record.lineno),
                    str(record.getMessage()),
                    str(json.dumps(list(active_coroutines))),
                    int(eval_loop_num)
                )
                
                cursor.execute("""
                    INSERT INTO logs (
                        id, timestamp, levelname, name, 
                        pathname, funcName, lineno, message, 
                        active_coroutines, eval_loop_num
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, values)
                
                self._maintain_log_limit(cursor)
                
                conn.commit()
            finally:
                cursor.close()
                conn.close()
            
        except Exception as e:
            print(f"Error writing to database: {str(e)}")
            self.handleError(record)

class ColoredFormatter(logging.Formatter):
    """Formats the log message with colors and ANSI escape codes for console output."""
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

def get_logger(name: str):
    """Get a logger with the given name."""
    logger = logging.getLogger(name.split(".")[-1])
    logger.propagate = False
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

    database_handler = DatabaseHandler()
    logger.addHandler(database_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(colored_formatter)
    logger.addHandler(console_handler)

    logger.debug(f"Logging mode is {logging.getLevelName(logger.getEffectiveLevel())}")
    return logger
