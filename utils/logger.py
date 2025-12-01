import os
import inspect
import logging

from datetime import datetime



# We want some loggers from third-party libraries to be quieter
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('chain_utils').setLevel(logging.WARNING)



LEVEL_NAME_TO_COLOR = {
    'DEBUG':   '\033[90m', # Gray
    'INFO':    '\033[32m', # Green
    'WARNING': '\033[33m', # Yellow
    'ERROR':   '\033[31m', # Red
    'FATAL':   '\033[31m'  # Red
}

GRAY = '\033[90m'
RESET = '\033[0m'



def print_log(level: str, message: str):
    now = datetime.now()
    timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
    ms = now.microsecond // 1000

    frame = inspect.currentframe().f_back
    file = frame.f_code.co_filename.split('/')[-1]
    line = frame.f_lineno
    
    print(f"{timestamp}.{ms:03d} - {file}:{line} - [{LEVEL_NAME_TO_COLOR[level]}{level}{RESET}] - {message}")



def debug(message: str):
    if os.getenv('DEBUG', 'false').lower() == 'true':
        print_log('DEBUG', GRAY + message + RESET)

def info(message: str):
    print_log('INFO', message)

def warning(message: str):
    print_log('WARNING', message)

def error(message: str):
    print_log('ERROR', message)

def fatal(message: str):
    print_log('FATAL', message)
    raise Exception(message)