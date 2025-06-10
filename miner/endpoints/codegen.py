import json
import os
import tempfile
import subprocess
import time
from pathlib import Path
import shutil
import threading

from shared.logging_utils import get_logger
from fastapi import APIRouter, Depends, Request, HTTPException, Header, Path as FastAPIPath

from miner.dependancies import blacklist_low_stake, verify_request, get_config
from miner.core.config import Config
from miner.utils.shared import worker_manager
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
        # Increase/decrease this to change speed and cost of the agent
        "--agent.model.per_instance_cost_limit=3.00",  
        f"--env.repo.path={repo_path}",
        f"--problem_statement.path={problem_file}",
        f"--output_dir={out_dir}"
    ]
    
    # Log the command being run
    logger.info(f"Running SWE-agent with command: {' '.join(cmd)}")
    
    # Start the process with real-time output streaming
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # Line buffered
        universal_newlines=True
    )

    # Function to stream output in real-time
    def stream_output(pipe, prefix):
        for line in pipe:
            logger.info(f"{prefix}: {line.strip()}")
    
    # Start threads to stream stdout and stderr
    stdout_thread = threading.Thread(target=stream_output, args=(process.stdout, "SWE-agent stdout"))
    stderr_thread = threading.Thread(target=stream_output, args=(process.stderr, "SWE-agent stderr"))
    stdout_thread.daemon = True
    stderr_thread.daemon = True
    stdout_thread.start()
    stderr_thread.start()

    # Also start a thread to monitor the log files
    def monitor_logs():
        while process.poll() is None:  # While process is running
            instance_dirs = list(Path(out_dir).glob("*"))
            if instance_dirs:
                instance_dir = max(instance_dirs, key=lambda p: p.stat().st_mtime)
                for log_file in instance_dir.glob("*.log"):
                    try:
                        with open(log_file, 'r') as f:
                            # Read new lines
                            f.seek(0, 2)  # Seek to end
                            while process.poll() is None:
                                line = f.readline()
                                if line:
                                    logger.info(f"SWE-agent log [{log_file.name}]: {line.strip()}")
                                else:
                                    time.sleep(0.1)  # Short sleep to prevent busy waiting
                    except Exception as e:
                        logger.error(f"Error reading log file {log_file}: {e}")
            time.sleep(1)  # Check for new instance directories every second

    log_monitor_thread = threading.Thread(target=monitor_logs)
    log_monitor_thread.daemon = True
    log_monitor_thread.start()

    # Wait for the process to complete
    try:
        process.wait(timeout=900)  # 15 minute timeout
    except subprocess.TimeoutExpired:
        process.kill()
        raise RuntimeError("SWE-agent timed out after 15 minutes")

    # Wait for output threads to finish
    stdout_thread.join()
    stderr_thread.join()
    log_monitor_thread.join()

    # 3) Find and read the patch file
    # First try the instance-specific directory
    instance_dirs = list(Path(out_dir).glob("*"))
    if not instance_dirs:
        logger.error(f"No instance directories found in {out_dir}")
        raise RuntimeError("SWE-agent did not create any output directories")

    # Get the most recent instance directory
    instance_dir = max(instance_dirs, key=lambda p: p.stat().st_mtime)
    logger.info(f"Using instance directory: {instance_dir}")

    if process.returncode != 0:
        # Check for UnicodeDecodeError in log files
        error_detected = False
        for log_file in instance_dir.glob("*.log"):
            with open(log_file, "r") as lf:
                if "UnicodeDecodeError" in lf.read():
                    error_detected = True
                    break
        if error_detected:
            logger.warning("SWE-agent failed due to UnicodeDecodeError in subprocess. Returning empty patch.")
            return ""
        raise RuntimeError(f"SWE-agent failed with return code {process.returncode}")

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

    # Log the cost of the run if available
    cost_file = instance_dir / "cost.json"
    if cost_file.exists():
        with open(cost_file, "r") as cf:
            cost_data = json.load(cf)
            logger.info(f"SWE-agent cost for this run: {cost_data}")
    else:
        logger.warning(f"No cost.json found in {instance_dir}")

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

async def process_challenge(
    request: Request,
    config: Config = Depends(get_config)
):
    try:
        challenge_data = await request.json()
        challenge_id = challenge_data.get("challenge_id")
        validator_hotkey = request.headers.get("validator-hotkey")
        
        if not validator_hotkey:
            logger.error("Missing validator-hotkey header")
            raise HTTPException(status_code=400, detail="Missing validator-hotkey header")
        
        logger.info(f"Received challenge {challenge_id} from validator {validator_hotkey}")
        
        # Validate challenge data
        problem_statement = challenge_data.get("problem_statement")
        dynamic_checklist = challenge_data.get("dynamic_checklist")
        repository = challenge_data.get("repository_url")
        commit_hash = challenge_data.get("commit_hash")
        
        if not problem_statement or not dynamic_checklist:
            logger.error("Incomplete problem provided")
            raise HTTPException(status_code=400, detail="Incomplete problem provided")
            
        if not repository:
            logger.error("repository_url is required")
            raise HTTPException(status_code=400, detail="repository_url is required")
        
        # Add to queue instead of acquiring lock
        success = await worker_manager.challenge_queue.add_challenge(
            challenge_id=challenge_id,
            validator_hotkey=validator_hotkey,
            data=challenge_data
        )
        
        if not success:
            logger.error(f"Failed to add challenge {challenge_id} to queue - queue is full")
            raise HTTPException(status_code=503, detail="Challenge queue is full")
            
        response_payload = {"success": True, "message": f"Challenge {challenge_id} added to queue"}
        logger.info(f"Returning POST body to validator: {response_payload}")
        return response_payload
        
    except Exception as e:
        logger.error(f"Error processing challenge: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# Create router instance
router = APIRouter()

# Define the endpoint with verification
@router.post("/challenge", dependencies=[Depends(verify_request)])
async def receive_challenge(request: Request, config: Config = Depends(get_config)):
    return await process_challenge(request, config)

# Add a new endpoint to get the result of a challenge
@router.get("/challenge/{challenge_id}", dependencies=[Depends(verify_request)])
async def get_challenge_result(
    challenge_id: str = FastAPIPath(...),
    config: Config = Depends(get_config)
):
    """Get the result of a challenge by its ID"""
    logger.info(f"Getting result for challenge {challenge_id}")
    
    # Check if we have a response for this challenge
    response = worker_manager.get_response(challenge_id)
    
    if not response:
        # Check if the challenge is still in the queue
        if challenge_id in worker_manager.challenge_queue.active_challenges:
            logger.info(f"Challenge {challenge_id} is still being processed")
            return {
                "challenge_id": challenge_id, 
                "status": "processing",
                "message": "Challenge is still being processed"
            }
        else:
            logger.warning(f"No response found for challenge {challenge_id}")
            raise HTTPException(status_code=404, detail=f"Challenge {challenge_id} not found")
    
    # Include any error information in the response
    if "error" in response and response["error"]:
        logger.warning(f"Challenge {challenge_id} has error: {response['error']}")
        return {
            "challenge_id": challenge_id,
            "status": "error",
            "error": response["error"],
            "patch": response.get("patch")
        }
    
    logger.info(f"Returning result for challenge {challenge_id}")
    return {
        "challenge_id": challenge_id,
        "status": "completed",
        "patch": response.get("patch")
    }
