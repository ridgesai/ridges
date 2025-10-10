import logging



# We want some loggers from third-party libraries to be quieter
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('chain_utils').setLevel(logging.WARNING)



LEVEL_NAME_TO_COLOR = {
    'DEBUG':   '\033[36m', # Cyan
    'INFO':    '\033[32m', # Green
    'WARNING': '\033[33m', # Yellow
    'ERROR':   '\033[31m'  # Red
}

GRAY = '\033[90m'
RESET = '\033[0m'



class ColoredFormatter(logging.Formatter):
    def format(self, record):
        formatted = super().format(record)
        
        level_name = record.levelname
        if level_name in LEVEL_NAME_TO_COLOR:
            colored_level = f"[{LEVEL_NAME_TO_COLOR[level_name]}{level_name}{RESET}]"
            formatted = formatted.replace(level_name, colored_level, 1)
        
        return formatted



logger = logging.getLogger('ridges')
logger.setLevel(logging.INFO)

formatter = ColoredFormatter(
    '%(asctime)s.%(msecs)03d - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

handler = logging.StreamHandler()
handler.setFormatter(formatter)
logger.addHandler(handler)

logger.propagate = False



def debug(message: str):
    for line in message.split('\n'):
        logger.debug(GRAY + line + RESET, stacklevel=2)

def info(message: str):
    for line in message.split('\n'):
        logger.info(line, stacklevel=2)

def warning(message: str):
    for line in message.split('\n'):
        logger.warning(line, stacklevel=2)

def error(message: str):
    for line in message.split('\n'):
        logger.error(LEVEL_NAME_TO_COLOR['ERROR'] + line + RESET, stacklevel=2)

def fatal(message: str):
    error(message)
    raise Exception(message)