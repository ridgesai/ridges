# ANSI color codes
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    # Regular colors
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    GRAY = '\033[90m'



def info(msg):
    print(f"{Colors.CYAN} INFO: {msg}{Colors.RESET}")

def warn(msg):
    print(f"{Colors.YELLOW} WARN: {msg}{Colors.RESET}")

def error(msg):
    print(f"{Colors.RED}ERROR: {msg}{Colors.RESET}")
    exit(1)



_verbose_mode = False

def enable_verbose():
    global _verbose_mode
    _verbose_mode = True

def debug(msg):
    if _verbose_mode:
        print(f"{Colors.GRAY}DEBUG: {msg}{Colors.RESET}")