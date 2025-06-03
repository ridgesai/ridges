import json

from ridges_logging.logging_utils import get_logger
from fastapi import APIRouter, Depends, Request, HTTPException

from miner.dependancies import blacklist_low_stake, verify_request, get_config
from miner.core.config import Config
from miner.utils.shared import miner_lock

logger = get_logger(__name__)

HELLO_WORLD_REGRESSION_DIFF = """diff --git a/test_fix.py b/test_fix.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/test_fix.py
@@ -0,0 +1,3 @@
+# Fixed regression issue
+def fix_failing_test():
+    return True
"""


async def process_regression_challenge(
    request: Request,
    config: Config = Depends(get_config)
):
    """
    Placeholder for regression challenge processing.
    """
    logger.info("Attempting to acquire miner lock...")
    async with miner_lock:
        logger.info("Miner lock acquired, processing regression challenge...")
        try:
            challenge_data = await request.json()
            challenge_id = challenge_data.get("challenge_id")
            problem_statement = challenge_data.get("problem_statement")
            repository_url = challenge_data.get("repository_url")
            context_file_paths = challenge_data.get("context_file_paths")

            # Optionally we need to check out a repo at a given commit and solve the regression there
            commit_hash = challenge_data.get("commit_hash")

            logger.info(f"Received regression challenge data: {json.dumps(challenge_data, indent=2)}")
            
            if not problem_statement or not repository_url:
                raise HTTPException(status_code=400, detail="Incomplete regression problem provided")
            
            logger.info(f"Processing regression challenge {challenge_id} with problem statement {problem_statement}")
            
            response = {
                "challenge_id": challenge_id,
                "patch": HELLO_WORLD_REGRESSION_DIFF,
            }
            
            logger.info(f"Responded to regression challenge {challenge_id}")
            return response
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error processing regression challenge: {str(e)}")
            logger.exception("Full error traceback:")
            raise HTTPException(status_code=500, detail=f"Regression challenge processing error: {str(e)}")
        finally:
            logger.info("Releasing miner lock...")


# Create router with dependencies
router = APIRouter()
router.add_api_route(
    "/challenge",
    process_regression_challenge,
    tags=["regression"],
    dependencies=[Depends(verify_request)],
    methods=["POST"],
) 