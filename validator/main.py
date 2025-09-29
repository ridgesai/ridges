import asyncio
import docker
import subprocess
import shutil
from pathlib import Path

from dotenv import load_dotenv
load_dotenv("validator/.env")

# Internal package imports
from validator.socket.websocket_app import WebsocketApp
from loggers.logging_utils import get_logger
from validator.utils.logger import enable_verbose
from validator.sandbox.sandbox_manager import SandboxManager
from validator.problem_suites.swebench_verified.swebench_verified_suite import SWEBenchVerifiedSuite
from validator.config import RIDGES_PROXY_URL

logger = get_logger(__name__)


def init_all_images():
    SWEBENCH_VERIFIED_PROBLEMS = [
        "astropy__astropy-13398", "astropy__astropy-13579", "django__django-11138",
        "django__django-11400", "django__django-12325", "django__django-12708",
        "django__django-13128", "django__django-13212", "django__django-13449",
        "django__django-13837", "django__django-14007", "django__django-14011",
        "django__django-14631", "django__django-15268", "django__django-15503",
        "django__django-15629", "django__django-15957", "django__django-16263",
        "django__django-16560", "django__django-16631", "pytest-dev__pytest-5787",
        "pytest-dev__pytest-6197", "pytest-dev__pytest-10356",
        "sphinx-doc__sphinx-9461", "sphinx-doc__sphinx-11510"
    ]
    manager = SandboxManager(RIDGES_PROXY_URL)
    swebench_verified_suite = SWEBenchVerifiedSuite(Path(__file__).parent / "datasets" / "swebench_verified")
    swebench_verified_suite.prebuild_problem_images(manager, SWEBENCH_VERIFIED_PROBLEMS)




async def main():
    """
    This starts up the validator websocket, which connects to the Ridges platform 
    It receives and sends events like new agents to evaluate, eval status, scores, etc
    """

    # enable_verbose()

    init_all_images()

    websocket_app = WebsocketApp()
    websocket_app.status_running = False
    try:
        await websocket_app.start()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        await websocket_app.shutdown()
        logger.info("Shutdown complete")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
