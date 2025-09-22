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
from validator.problem_suites.swebench_verified.swebench_verified_suite import SWEBENCH_VERIFIED_PROBLEMS

logger = get_logger(__name__)


def init_all_images():
    manager = SandboxManager(RIDGES_PROXY_URL)
    swebench_verified_suite = SWEBenchVerifiedSuite(Path(__file__) / "datasets" / "swebench_verified")
    swebench_verified_suite.prebuild_problem_images(manager, SWEBENCH_VERIFIED_PROBLEMS)




async def main():
    """
    This starts up the validator websocket, which connects to the Ridges platform 
    It receives and sends events like new agents to evaluate, eval status, scores, etc
    """

    # enable_verbose()

    init_all_images()

    websocket_app = WebsocketApp()
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
