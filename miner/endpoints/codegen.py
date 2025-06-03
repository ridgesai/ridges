import json
import os
import tempfile
import subprocess
from pathlib import Path

from shared.logging_utils import get_logger
from fastapi import APIRouter, Depends, Request, HTTPException

from miner.dependancies import blacklist_low_stake, verify_request, get_config
from miner.core.config import Config
from miner.utils.shared import miner_lock
from miner.utils.git_ops import clone_and_checkout_repo
from miner.utils.patch import generate_patch, apply_patch
from validator.challenge.common import File

logger = get_logger(__name__)

HELLO_WORLD_DIFF = """diff --git a/newfolder/main.py b/newfolder/main.py
new file mode 100644
index 0000000..df1dc68
--- /dev/null
+++ b/newfolder/main.py
@@ -0,0 +1 @@
+print('Hello World')
"""

# Add new helper function for SWE-agent CLI integration
def solve_with_swe_agent(problem_text: str, repo_path: str) -> str:
    """Runs SWE-agent CLI once and returns the produced git diff as a string."""
    # 1) put problem statement in a tmp markdown file
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
        f.write(problem_text)
        problem_file = f.name

    # 2) run the agent
    out_dir = tempfile.mkdtemp(prefix="sweagent_run_")
    cmd = [
        "sweagent", "run",
        "--agent.model.name=claude-3-7-sonnet-latest",
        "--agent.model.per_instance_cost_limit=2.00",
        f"--env.repo.path={repo_path}",
        f"--problem_statement.path={problem_file}",
        f"--output_dir={out_dir}"           # Changed from output_path to output_dir
    ]
    
    # Log the command being run
    logger.info(f"Running SWE-agent with command: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    
    # Log the full output
    logger.info(f"SWE-agent stdout: {result.stdout}")
    logger.info(f"SWE-agent stderr: {result.stderr}")
    
    if result.returncode != 0:
        raise RuntimeError(f"SWE-agent failed: {result.stderr}")

    # 3) Find and read the patch file
    # First try the instance-specific directory
    instance_dirs = list(Path(out_dir).glob("*"))
    if not instance_dirs:
        logger.error(f"No instance directories found in {out_dir}")
        raise RuntimeError("SWE-agent did not create any output directories")

    # Get the most recent instance directory
    instance_dir = max(instance_dirs, key=lambda p: p.stat().st_mtime)
    logger.info(f"Using instance directory: {instance_dir}")

    # Look for the patch file
    patch_file = instance_dir / f"{instance_dir.name}.patch"
    if not patch_file.exists():
        logger.error(f"Patch file not found at {patch_file}")
        logger.error(f"Directory contents: {list(instance_dir.glob('*'))}")
        raise RuntimeError("SWE-agent did not generate a patch file")

    # Read the patch file
    logger.info(f"Reading patch from {patch_file}")
    with open(patch_file, "r") as fh:
        diff = fh.read()

    if not diff:
        logger.error("Patch file is empty")
        raise RuntimeError("SWE-agent generated an empty patch")

    # Clean up the patch:
    # 1. Remove __pycache__ entries
    # 2. Remove trailing whitespace
    lines = diff.splitlines()
    cleaned_lines = []
    skip_file = False
    
    for line in lines:
        # Skip __pycache__ files
        if "__pycache__" in line:
            skip_file = True
            continue
        if skip_file and line.startswith("diff --git"):
            skip_file = False
        if skip_file:
            continue
            
        # Remove trailing whitespace
        cleaned_lines.append(line.rstrip())
    
    diff = "\n".join(cleaned_lines) + "\n"
    
    logger.info(f"Successfully cleaned patch of length {len(diff)}")
    return diff

# --- New Ollama helper -----------------------------------------------
def solve_with_ollama_swe_agent(problem_text: str, repo_path: str, model_name: str = "llama3") -> str:
    """
    Runs SWE-agent CLI with a local Ollama model and returns the produced git diff as a string.
    """
    import tempfile
    from pathlib import Path
    import subprocess

    logger.info(f"Using Ollama model: {model_name}")

    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
        f.write(problem_text)
        problem_file = f.name

    out_dir = tempfile.mkdtemp(prefix="sweagent_run_")
    cmd = [
        "sweagent", "run",
        f"--agent.model.name=ollama/{model_name}",
        "--agent.model.api_base=http://localhost:11434",
        "--agent.model.per_instance_cost_limit=0",
        "--agent.model.total_cost_limit=0",
        "--agent.model.per_instance_call_limit=100",
        "--agent.model.max_input_tokens=0",
        "--agent.tools.parse_function.type=function_calling",  # Faster than thought_action
        f"--env.repo.path={repo_path}",
        f"--problem_statement.path={problem_file}",
        f"--output_dir={out_dir}"
    ]

    logger.info(f"Running SWE-agent with Ollama: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    logger.info(f"SWE-agent stdout: {result.stdout}")
    logger.info(f"SWE-agent stderr: {result.stderr}")

    if result.returncode != 0:
        raise RuntimeError(f"SWE-agent failed: {result.stderr}")

    # Find and read the patch file (same as your current logic)
    instance_dirs = list(Path(out_dir).glob("*"))
    if not instance_dirs:
        logger.error(f"No instance directories found in {out_dir}")
        raise RuntimeError("SWE-agent did not create any output directories")

    instance_dir = max(instance_dirs, key=lambda p: p.stat().st_mtime)
    logger.info(f"Using instance directory: {instance_dir}")

    patch_file = instance_dir / f"{instance_dir.name}.patch"
    if not patch_file.exists():
        logger.error(f"Patch file not found at {patch_file}")
        logger.error(f"Directory contents: {list(instance_dir.glob('*'))}")
        raise RuntimeError("SWE-agent did not generate a patch file")

    logger.info(f"Reading patch from {patch_file}")
    with open(patch_file, "r") as fh:
        diff = fh.read()

    if not diff:
        logger.error("Patch file is empty")
        raise RuntimeError("SWE-agent generated an empty patch")

    # Clean up the patch
    lines = diff.splitlines()
    cleaned_lines = []
    skip_file = False
    for line in lines:
        if "__pycache__" in line:
            skip_file = True
            continue
        if skip_file and line.startswith("diff --git"):
            skip_file = False
        if skip_file:
            continue
        cleaned_lines.append(line.rstrip())
    diff = "\n".join(cleaned_lines) + "\n"
    logger.info(f"Successfully cleaned patch of length {len(diff)}")
    return diff

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
            repository = challenge_data.get("repository_url")
            commit_hash = challenge_data.get("commit_hash")

            logger.info(f"Received challenge data: {json.dumps(challenge_data, indent=2)}")
            
            if not problem_statement or not dynamic_checklist:
                raise HTTPException(status_code=400, detail="Incomplete problem provided")
            
            # Check for OpenAI API key
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                logger.error("OpenAI API key not set in environment")
                raise HTTPException(status_code=500, detail="OpenAI API key not set in environment")

            if not repository:
                raise HTTPException(status_code=400, detail="repository_name is required")

            logger.info(f"Cloning repository {repository} at commit {commit_hash}")
            repo_path = clone_and_checkout_repo(repository, commit_hash)

            # get python paths from challenge_data.get("context_file_paths") relative to repo_path
            # Default to empty list if context_file_paths is not provided
            context_file_paths = challenge_data.get("context_file_paths", [])
            paths = [os.path.join(repo_path, file) for file in context_file_paths]

            # get the contents of the python paths
            relevant_files = [File(path=file, contents=open(file, "r").read()) for file in paths if os.path.exists(file)]
            logger.info(f"Repository cloned to {repo_path}")
            
            logger.info(f"Processing challenge {challenge_id} with problem statement {problem_statement}")

            # Generate solution using SWE-agent (should be a patch/diff)
            logger.info("Generating solution using SWE-agent...")

            # Ollama SWE-agent
            solution_patch = solve_with_ollama_swe_agent(problem_statement, repo_path)

            # Replace with SWE-agent CLI call
            # solution_patch = solve_with_swe_agent(problem_statement, repo_path)

            # For testing: Return hello world diff
            # solution_patch = HELLO_WORLD_DIFF
            
            logger.info(f"Generated solution patch: {solution_patch}")

            # Post-process patch: ensure it ends with a single newline, no trailing whitespace, no extra blank lines
            solution_patch = solution_patch.rstrip() + "\n"

            # Validate patch format
            if not solution_patch.strip().startswith("diff --git"):
                logger.error("LLM output is not a valid git diff (patch). Output was: %s", solution_patch)
                raise HTTPException(status_code=500, detail="LLM did not return a valid git diff (patch).")

            # Apply the patch to the repo
            try:
                apply_patch(repo_path, solution_patch)
                logger.info("Patch applied successfully.")
            except Exception as e:
                logger.error(f"Failed to apply patch: {e}")
                # Note: SWE-agent may handle retries internally, so we log the error but do not retry here.
                raise HTTPException(status_code=500, detail=f"Failed to apply patch: {str(e)}")

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
    # dependencies=[Depends(verify_request)],
    methods=["POST"],
)
