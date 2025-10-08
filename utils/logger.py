import logging



class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG':    '\033[36m', # Cyan
        'INFO':     '\033[32m', # Green
        'WARNING':  '\033[33m', # Yellow
        'ERROR':    '\033[31m'  # Red
    }

    RESET = '\033[0m'
    
    def format(self, record):
        formatted = super().format(record)
        
        level_name = record.levelname
        if level_name in self.COLORS:
            colored_level = f"[{self.COLORS[level_name]}{level_name.ljust(8)}{self.RESET}]"
            formatted = formatted.replace(level_name, colored_level, 1)
        
        return formatted



logger = logging.getLogger('ridges')
logger.setLevel(logging.DEBUG)

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
        logger.debug(line)

def info(message: str):
    for line in message.split('\n'):
        logger.info(line)

def warning(message: str):
    for line in message.split('\n'):
        logger.warning(line)

def error(message: str):
    for line in message.split('\n'):
        logger.error(line)

def fatal(message: str):
    for line in message.split('\n'):
        logger.error(line)
        
    exit(1)