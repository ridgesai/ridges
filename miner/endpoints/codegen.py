import json
import os

from logging.logging_utils import get_logger
from fastapi import APIRouter, Depends, Request, HTTPException

from miner.dependancies import blacklist_low_stake, verify_request, get_config
from miner.core.config import Config
from miner.utils.shared import miner_lock
from miner.utils.git_ops import clone_and_checkout_repo
from miner.utils.llm import generate_solution_with_openai
from miner.utils.patch import generate_patch

logger = get_logger(__name__)

HELLO_WORLD_DIFF = """diff --git a/newfolder/main.py b/newfolder/main.py
new file mode 100644
index 0000000..df1dc68
--- /dev/null
+++ b/newfolder/main.py
@@ -0,0 +1 @@
+print('Hello World')
"""


async def process_challenge(
    request: Request,
    config: Config = Depends(get_config)
):
    logger.info("Attempting to acquire miner lock...")
    async with miner_lock:
        logger.info("Miner lock acquired, processing challenge...")
        try:
            challenge_data = await request.json()
            challenge_id = challenge_data.get("challenge_id")
            problem_statement = challenge_data.get("problem_statement")
            dynamic_checklist = challenge_data.get("dynamic_checklist")
            repository_url = challenge_data.get("repository_url")

            # Optionally we need to check out a repo at a given commit and solve the problem there
            commit_hash = challenge_data.get("commit_hash")

            logger.info(f"Received challenge data: {json.dumps(challenge_data, indent=2)}")
            
            if not problem_statement or not dynamic_checklist:
                raise HTTPException(status_code=400, detail="Incomplete problem provided")
            
            # Check for OpenAI API key
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                logger.error("OpenAI API key not set in environment")
                raise HTTPException(status_code=500, detail="OpenAI API key not set in environment")

            logger.info(f"Cloning repository {repository_url} at commit {commit_hash}")
            repo_path = clone_and_checkout_repo(repository_url, commit_hash)
            logger.info(f"Repository cloned to {repo_path}")
            
            logger.info(f"Processing challenge {challenge_id} with problem statement {problem_statement}")

            # Generate solution using OpenAI
            logger.info("Generating solution using OpenAI...")
            solution = generate_solution_with_openai(problem_statement, api_key)
            logger.info(f"Generated solution: {solution}")

            # Write solution to a new file in the cloned repo
            solution_file = os.path.join(repo_path, "solution.py")
            with open(solution_file, "w") as f:
                f.write(solution)
            logger.info(f"Solution written to {solution_file}")

            # Generate a git patch of the changes
            patch = generate_patch(repo_path)
            logger.info(f"Generated patch:\n{patch}")

            response = {
                "challenge_id": challenge_id,
                "patch": patch,
            }
            
            logger.info(f"Responded to challenge {challenge_id}")
            return response
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error processing soccer challenge: {str(e)}")
            logger.exception("Full error traceback:")
            raise HTTPException(status_code=500, detail=f"Challenge processing error: {str(e)}")
        finally:
            logger.info("Releasing miner lock...")


# Create router with dependencies
router = APIRouter()
router.add_api_route(
    "/challenge",
    process_challenge,
    tags=["codegen"],
    # Commnent out dependencies for testing
    dependencies=[Depends(verify_request)],
    methods=["POST"],
)