import asyncio
import threading
import tempfile
from pathlib import Path
import time
import os
import yaml
import subprocess
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional, List, Tuple, Union

from shared.logging_utils import get_logger
from miner.core.queue import ChallengeQueue, Challenge
from miner.utils.git_ops import clone_and_checkout_repo
from miner.utils.patch import generate_patch, apply_patch

logger = get_logger(__name__)

class WorkerManager:
    def __init__(self, num_workers: int = 1, max_queue_size: int = 1):
        """Initialize the worker manager with a specified number of workers"""
        self.num_workers = num_workers
        self.challenge_queue = ChallengeQueue(max_size=max_queue_size)
        self.worker_task = None
        self.running = False
        self.worker_processes = {}  # Maps challenge_id to process info
        self.responses = {}  # Stores responses to send back to validators
        
    async def start(self):
        """Start the worker manager"""
        if self.running:
            return
            
        self.running = True
        # Start a single task to manage all workers
        self.worker_task = asyncio.create_task(self._worker_loop())
        logger.info(f"Started worker manager with {self.num_workers} workers")
        
    async def stop(self):
        """Stop the worker manager"""
        if not self.running:
            return
            
        self.running = False
        
        if self.worker_task:
            self.worker_task.cancel()
            
        # Terminate any running processes
        for challenge_id, process_info in list(self.worker_processes.items()):
            process = process_info.get("process")
            if process and process.poll() is None:
                logger.info(f"Terminating process for challenge {challenge_id}")
                process.terminate()
                
        self.worker_processes = {}
        logger.info("Worker manager stopped")
        
    async def _worker_loop(self):
        """Main loop for worker management"""
        logger.info("Worker loop started")
        
        # Use a thread pool to manage worker processes
        with ThreadPoolExecutor(max_workers=self.num_workers) as executor:
            while self.running:
                try:
                    # Check if we can start a new worker
                    active_workers = len([p for p in self.worker_processes.values() 
                                         if p.get("process") and p.get("process").poll() is None])
                    
                    if active_workers >= self.num_workers:
                        # Maximum workers running, wait a bit
                        await asyncio.sleep(1)
                        continue
                        
                    # Try to get a challenge
                    challenge = await self.challenge_queue.get_next_challenge()
                    
                    if not challenge:
                        await asyncio.sleep(0.1)
                        continue
                        
                    logger.info(f"Starting worker for challenge {challenge.challenge_id}")
                    
                    # Start a worker process for this challenge
                    executor.submit(
                        self._process_challenge,
                        challenge
                    )
                    
                except asyncio.CancelledError:
                    logger.info("Worker loop cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in worker loop: {str(e)}", exc_info=True)
                    await asyncio.sleep(1)
                    
        logger.info("Worker loop stopped")
    
    def get_response(self, challenge_id: str) -> Optional[Dict[str, Any]]:
        """Get a stored response for a challenge ID"""
        return self.responses.get(challenge_id)
    
    def _run_git_command(self, repo_path: Path, command: List[str], 
                         capture_output: bool = True) -> Tuple[int, str, str]:
        """Safely run a git command in the repo and return result"""
        try:
            result = subprocess.run(
                command,
                cwd=repo_path,
                capture_output=capture_output,
                text=True,
                check=False
            )
            return result.returncode, result.stdout if capture_output else "", result.stderr if capture_output else ""
        except Exception as e:
            logger.error(f"Error running git command {command}: {e}")
            return 1, "", str(e)
    
    def _extract_created_files_from_logs(self, output_dir: Path) -> List[str]:
        """Extract file paths that SWE-agent created from logs"""
        created_files = []
        
        # Check log files
        for log_file in output_dir.glob("**/*.log"):
            try:
                with open(log_file, "r", errors="replace") as f:
                    for line in f:
                        if "File created successfully at:" in line:
                            parts = line.split("File created successfully at:")
                            if len(parts) > 1:
                                path = parts[1].strip()
                                if path.startswith("/"):
                                    created_files.append(path)
            except Exception as e:
                logger.debug(f"Error reading log file {log_file}: {e}")
                
        # Also check for any other file paths in JSON output
        for json_file in output_dir.glob("**/*.json"):
            try:
                with open(json_file, "r") as f:
                    data = json.load(f)
                    # Convert to string to search
                    content = json.dumps(data)
                    # Simple regex to find paths
                    paths = re.findall(r'["\']?/[a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+["\']?', content)
                    for path in paths:
                        # Clean up quotes
                        clean_path = path.strip('"\'')
                        if os.path.isfile(clean_path):
                            created_files.append(clean_path)
            except Exception as e:
                logger.debug(f"Error processing JSON file {json_file}: {e}")
        
        return created_files
        
    def _process_challenge(self, challenge: Challenge):
        """Process a challenge using SWE-agent's batch mode"""
        try:
            challenge_id = challenge.challenge_id
            data = challenge.data
            
            logger.info(f"Processing challenge {challenge_id}")
            
            # Extract challenge data
            problem_statement = data.get("problem_statement", "")
            repository_url = data.get("repository_url", "")
            commit_hash = data.get("commit_hash", "")
            
            # Clone the repository first
            try:
                logger.info(f"Cloning repository {repository_url} at commit {commit_hash}")
                repo_path = clone_and_checkout_repo(repository_url, commit_hash)
                logger.info(f"Repository cloned to {repo_path}")
            except Exception as e:
                logger.error(f"Failed to clone repository: {str(e)}")
                # Store error response
                self.responses[challenge_id] = {
                    "challenge_id": challenge_id,
                    "error": f"Failed to clone repository: {str(e)}",
                    "patch": None
                }
                asyncio.run(self.challenge_queue.complete_challenge(challenge_id))
                return None
            
            # Create a temporary directory for batch config
            work_dir = tempfile.mkdtemp(prefix=f"challenge_{challenge_id}_")
            
            # Create output directory
            output_dir = Path(work_dir) / "output"
            output_dir.mkdir(exist_ok=True)
            
            # Get the project root directory
            project_root = Path(__file__).parent.parent.parent
            
            # Get SWE-agent config path - Look for it in SWE-agent directory
            swe_agent_config = project_root / "SWE-agent" / "config" / "default.yaml"
            
            if not swe_agent_config.exists():
                logger.warning(f"SWE-agent config not found at {swe_agent_config}, falling back to environment variable")
                # Fall back to environment variable or default
                config_path = os.environ.get("SWEAGENT_CONFIG_PATH", str(swe_agent_config))
            else:
                config_path = str(swe_agent_config)
                
            logger.info(f"Using SWE-agent config at: {config_path}")
            
            # Prepare SWE-agent single-instance command
            cmd = [
                "sweagent", "run",
                "--config", config_path,
                "--agent.model.name=claude-3-7-sonnet-latest",
                "--agent.model.per_instance_cost_limit=3.00",
                f"--env.repo.path={repo_path}",
            ]

            # Write problem statement to file and pass its path
            problem_file = Path(work_dir) / "problem_statement.txt"
            try:
                problem_file.write_text(problem_statement or "", encoding="utf-8")
                cmd.append(f"--problem_statement.path={problem_file}")
            except Exception as e:
                logger.warning(f"Failed to write problem statement to file: {e}. Passing as text instead.")
                if problem_statement:
                    cmd.append(f"--problem_statement.text={problem_statement}")

            # Set the output directory
            cmd.append(f"--output_dir={output_dir}")
            
            logger.info(f"Running SWE-agent for challenge {challenge_id}")
            logger.debug(f"Command: {' '.join(cmd)}")
            
            # Start the process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            # Store process info
            self.worker_processes[challenge_id] = {
                "process": process,
                "start_time": time.time(),
                "output_dir": output_dir,
                "challenge": challenge
            }
            
            # Patterns for important logs that should be shown at INFO level
            important_patterns = [
                r"Starting", r"Completed", r"Finished", r"Success", r"Error", r"Exception",
                r"^Task", r"Warning", r"Critical", r"Failure", r"Failed", r"Progress:"
            ]
            important_regex = re.compile("|".join(important_patterns), re.IGNORECASE)
            
            # Function to stream output with filtered logging
            def stream_output(pipe, prefix):
                for line in pipe:
                    line = line.strip()
                    if important_regex.search(line) or "error" in line.lower() or "exception" in line.lower():
                        # Important message - log at INFO level
                        logger.info(f"{prefix} [{challenge_id}]: {line}")
                    else:
                        # Regular message - log at DEBUG level
                        logger.debug(f"{prefix} [{challenge_id}]: {line}")
                    
            # Start threads to monitor output
            threading.Thread(target=stream_output, args=(process.stdout, "SWE-agent"), daemon=True).start()
            threading.Thread(target=stream_output, args=(process.stderr, "SWE-agent error"), daemon=True).start()
            
            # Wait for process to complete
            process.wait()
            
            # Process completed
            logger.info(f"Challenge {challenge_id} processing completed with return code {process.returncode}")
            
            # Log output directory contents for debugging
            self._log_output_directory_contents(output_dir)
            
            # Handle non-zero return code
            if process.returncode != 0:
                logger.error(f"SWE-agent process failed with return code {process.returncode}")
                # Even if SWE-agent failed, we still try to recover any work it did
                logger.info(f"Attempting to recover work from failed SWE-agent run for challenge {challenge_id}")
                # Continue with patch extraction - don't return early
            
            # Get the patch file - first try with instance_id
            patch_file = output_dir / f"{challenge_id}.patch"
            

            # These are just a crap ton of checks to try to find the patch file.
            # SWE-agent is a bit of a mess.
            # If not found, try other common patterns
            if not patch_file.exists():
                # Try looking in instance directory
                instance_dir = output_dir / challenge_id
                if instance_dir.exists():
                    patch_file = instance_dir / f"{challenge_id}.patch"
                    if not patch_file.exists():
                        # Try common alternative names
                        for alt_name in ["model.patch", "solution.patch", "changes.patch", "fix.patch", "output.patch"]:
                            alt_file = instance_dir / alt_name
                            if alt_file.exists():
                                patch_file = alt_file
                                logger.info(f"Found patch file with alternate name: {alt_file}")
                                break
                
                # If still not found, search in all subdirectories
                if not patch_file.exists():
                    # Look for any .patch file in output directories
                    patch_files = list(output_dir.glob("**/*.patch"))
                    if patch_files:
                        patch_file = patch_files[0]
                        logger.info(f"Found patch file at alternative location: {patch_file}")
                    else:
                        # Also check for .diff files as an alternative
                        diff_files = list(output_dir.glob("**/*.diff"))
                        if diff_files:
                            patch_file = diff_files[0]
                            logger.info(f"Found diff file instead of patch: {patch_file}")
                        else:
                            # Look for text files that might contain patch content
                            for txt_file in output_dir.glob("**/*.txt"):
                                try:
                                    with open(txt_file, "r") as f:
                                        content = f.read(200)  # Read first 200 chars to check
                                        if content.strip().startswith("diff --git"):
                                            patch_file = txt_file
                                            logger.info(f"Found patch content in text file: {txt_file}")
                                            break
                                except Exception as e:
                                    logger.debug(f"Error reading text file {txt_file}: {e}")
            
            # Check if SWE-agent's trajectory contains patch info (check JSON files)
            if not patch_file.exists():
                logger.info(f"No direct patch file found, checking trajectory files for challenge {challenge_id}")
                trajectory_files = list(output_dir.glob("**/*.json"))
                for t_file in trajectory_files:
                    try:
                        with open(t_file, "r") as f:
                            data = json.load(f)
                            # Check if this is a trajectory file with actions
                            if isinstance(data, dict) and "actions" in data:
                                for action in data.get("actions", []):
                                    # Look for submit actions which might contain patches
                                    if action.get("action_type") == "submit" and "patch" in action:
                                        patch_content = action.get("patch", "")
                                        if patch_content and patch_content.strip().startswith("diff --git"):
                                            # Create a patch file from this content
                                            extracted_patch_file = output_dir / f"{challenge_id}_extracted.patch"
                                            with open(extracted_patch_file, "w") as pf:
                                                pf.write(patch_content)
                                            patch_file = extracted_patch_file
                                            logger.info(f"Extracted patch from trajectory file: {t_file}")
                                            break
                    except Exception as e:
                        logger.debug(f"Error parsing JSON file {t_file}: {e}")
                    
                    if patch_file.exists():
                        break
            
            # If we still don't have a patch file, as a last resort, check if SWE-agent made any changes to the repo
            # and generate a patch ourselves
            if not patch_file.exists():
                logger.info(f"No patch file found in output, checking if repo has changes for challenge {challenge_id}")
                try:
                    # Check if the repo has any uncommitted changes
                    repo_patch = generate_patch(repo_path)
                    if repo_patch and repo_patch.strip().startswith("diff --git"):
                        # SWE-agent made changes but didn't generate a patch file
                        fallback_patch_file = output_dir / f"{challenge_id}_fallback.patch"
                        with open(fallback_patch_file, "w") as f:
                            f.write(repo_patch)
                        patch_file = fallback_patch_file
                        logger.info(f"Generated fallback patch from repo changes for challenge {challenge_id}")
                    else:
                        # No changes in repo, check if files were created elsewhere in the container
                        logger.info(f"No changes in repo, checking for files created outside the repo for challenge {challenge_id}")
                        
                        # Find files created by SWE-agent outside the repo
                        external_files = set()
                        
                        # Common hardcoded paths
                        common_paths = [
                            "/combined_kde_penguins.py",  # This appears in the logs
                            "/solution.py",
                            "/fix.py",
                            "/model.py",
                            "/output.py"
                        ]
                        
                        # Add files from logs
                        log_extracted_files = self._extract_created_files_from_logs(output_dir)
                        external_files.update(common_paths)
                        external_files.update(log_extracted_files)
                        
                        # Check if any of these files exist and try to copy them to the repo
                        copied_files = []
                        for file_path in external_files:
                            file_path = Path(file_path)
                            if file_path.exists() and file_path.is_file():
                                logger.info(f"Found file outside repo: {file_path}")
                                try:
                                    # Get the filename only
                                    filename = file_path.name
                                    # Copy to repo root
                                    target_path = repo_path / filename
                                    # Read content and write to target
                                    content = file_path.read_text(errors="replace")
                                    target_path.write_text(content)
                                    copied_files.append((filename, content))
                                    logger.info(f"Copied {file_path} to {target_path}")
                                except Exception as e:
                                    logger.error(f"Error copying file {file_path}: {e}")
                        
                        # If we copied files, generate a patch
                        if copied_files:
                            # Try to add to git and create patch
                            try:
                                # Initialize git repo if needed (in case it wasn't done properly)
                                try:
                                    # Set git config to ensure commits work
                                    self._run_git_command(repo_path, ["git", "config", "--global", "user.email", "miner@ridges.org"])
                                    self._run_git_command(repo_path, ["git", "config", "--global", "user.name", "Ridges Miner"])
                                except Exception as e:
                                    logger.debug(f"Git config error (non-critical): {e}")
                                    
                                # Add all files and create patch
                                for filename, _ in copied_files:
                                    self._run_git_command(repo_path, ["git", "add", filename])
                                
                                # Generate patch directly
                                code, stdout, stderr = self._run_git_command(repo_path, ["git", "diff", "--cached"])
                                
                                if code == 0 and stdout:
                                    # We have a patch
                                    manual_patch_file = output_dir / f"{challenge_id}_manual.patch"
                                    with open(manual_patch_file, "w") as f:
                                        f.write(stdout)
                                    patch_file = manual_patch_file
                                    logger.info(f"Generated manual patch from copied files for challenge {challenge_id}")
                                else:
                                    # If git commands failed, create a manual patch in unified diff format
                                    logger.info(f"Git diff failed, creating manual unified diff for challenge {challenge_id}")
                                    logger.debug(f"Git error: {stderr}")
                                    manual_diff = []
                                    for filename, content in copied_files:
                                        manual_diff.append(f"diff --git a/{filename} b/{filename}")
                                        manual_diff.append(f"new file mode 100644")
                                        manual_diff.append(f"--- /dev/null")
                                        manual_diff.append(f"+++ b/{filename}")
                                        manual_diff.append(f"@@ -0,0 +1,{len(content.splitlines())} @@")
                                        for line in content.splitlines():
                                            manual_diff.append(f"+{line}")
                                    
                                    manual_patch_content = "\n".join(manual_diff)
                                    manual_patch_file = output_dir / f"{challenge_id}_manual_diff.patch"
                                    with open(manual_patch_file, "w") as f:
                                        f.write(manual_patch_content)
                                    patch_file = manual_patch_file
                                    logger.info(f"Created manual unified diff for challenge {challenge_id}")
                            except Exception as e:
                                logger.error(f"Error generating manual patch: {e}")
                except Exception as e:
                    logger.error(f"Error checking repo for changes: {str(e)}")
                    
            # Default value if no patch file is found
            solution_patch = ""
            
            if patch_file.exists():
                try:
                    with open(patch_file, "r") as f:
                        solution_patch = f.read()
                        
                    logger.info(f"Found patch file for challenge {challenge_id}, length: {len(solution_patch)}")
                    
                    # Post-process patch: ensure it ends with a single newline
                    solution_patch = solution_patch.rstrip() + "\n"
                    
                    # Validate patch format
                    if not solution_patch.strip().startswith("diff --git"):
                        logger.error(f"Invalid patch format for challenge {challenge_id}")
                        self.responses[challenge_id] = {
                            "challenge_id": challenge_id,
                            "error": "Invalid patch format - does not start with 'diff --git'",
                            "patch": solution_patch  # Include the content for debugging
                        }
                    else:
                        # Try to apply the patch to the repo
                        try:
                            apply_patch(repo_path, solution_patch)
                            logger.info(f"Patch applied successfully for challenge {challenge_id}")
                            
                            # Generate a git patch of the changes
                            patch = generate_patch(repo_path)
                            logger.info(f"Generated patch for challenge {challenge_id}, length: {len(patch)}")
                            
                            # Store the response
                            self.responses[challenge_id] = {
                                "challenge_id": challenge_id,
                                "patch": patch,
                            }
                        except Exception as e:
                            logger.error(f"Failed to apply patch for challenge {challenge_id}: {str(e)}")
                            # Still store the original patch
                            self.responses[challenge_id] = {
                                "challenge_id": challenge_id,
                                "error": f"Failed to apply patch: {str(e)}",
                                "patch": solution_patch  # Include the original patch for debugging
                            }
                except Exception as e:
                    logger.error(f"Error reading or processing patch file: {str(e)}")
                    self.responses[challenge_id] = {
                        "challenge_id": challenge_id,
                        "error": f"Error reading or processing patch file: {str(e)}",
                        "patch": None
                    }
            else:
                # One final attempt - check if the repo actually has changes but we failed to generate a patch
                try:
                    # Generate a git patch directly
                    final_patch = generate_patch(repo_path)
                    if final_patch and final_patch.strip().startswith("diff --git"):
                        logger.info(f"Found uncommitted changes in repo for challenge {challenge_id}, using those as solution")
                        self.responses[challenge_id] = {
                            "challenge_id": challenge_id,
                            "patch": final_patch,
                        }
                    else:
                        # Check if we found any files that were created outside the repo
                        # These would be in the work_dir
                        external_patch_files = list(Path(work_dir).glob("**/*_manual*.patch")) + list(Path(work_dir).glob("**/*_fallback*.patch"))
                        if external_patch_files:
                            # Use the first found external patch
                            ext_patch_file = external_patch_files[0]
                            with open(ext_patch_file, "r") as f:
                                external_patch = f.read()
                            
                            if external_patch and external_patch.strip().startswith("diff --git"):
                                logger.info(f"Using manually generated patch from external files for challenge {challenge_id}")
                                self.responses[challenge_id] = {
                                    "challenge_id": challenge_id,
                                    "patch": external_patch,
                                }
                            else:
                                logger.warning(f"No valid patch found for challenge {challenge_id}")
                                self.responses[challenge_id] = {
                                    "challenge_id": challenge_id,
                                    "error": "No valid patch generated",
                                    "patch": None
                                }
                        else:
                            logger.warning(f"No patch file found for challenge {challenge_id}")
                            self.responses[challenge_id] = {
                                "challenge_id": challenge_id,
                                "error": "No patch file generated by SWE-agent",
                                "patch": None
                            }
                except Exception as e:
                    logger.error(f"Final attempt to generate patch failed: {str(e)}")
                    logger.warning(f"No patch file found for challenge {challenge_id}")
                    self.responses[challenge_id] = {
                        "challenge_id": challenge_id,
                        "error": "No patch file generated by SWE-agent",
                        "patch": None
                    }
                
            # Complete the challenge
            asyncio.run(self.challenge_queue.complete_challenge(challenge_id))
            
            # Clean up
            if challenge_id in self.worker_processes:
                del self.worker_processes[challenge_id]
                
        except Exception as e:
            logger.error(f"Error processing challenge {challenge.challenge_id}: {str(e)}", exc_info=True)
            try:
                # Store error response
                self.responses[challenge.challenge_id] = {
                    "challenge_id": challenge.challenge_id,
                    "error": f"Processing error: {str(e)}",
                    "patch": None
                }
                
                asyncio.run(self.challenge_queue.complete_challenge(challenge.challenge_id))
                if challenge.challenge_id in self.worker_processes:
                    del self.worker_processes[challenge.challenge_id]
            except Exception as cleanup_error:
                logger.error(f"Error cleaning up challenge: {str(cleanup_error)}")
            return None 

    def _log_output_directory_contents(self, output_dir: Path):
        """Log all output files in the given directory for debugging"""
        try:
            # Just log directory structure first
            logger.debug(f"Output directory structure:")
            file_list = list(output_dir.glob("**/*"))
            for file in file_list:
                if file.is_file():
                    logger.debug(f"  File: {file} ({file.stat().st_size} bytes)")
                elif file.is_dir():
                    logger.debug(f"  Dir: {file}")
            
            # Then try to read text files that might be helpful
            logger.debug(f"Checking for key files:")
            for file in file_list:
                if file.is_file() and file.suffix in ['.txt', '.log', '.yaml', '.json', '.patch']:
                    try:
                        # Read small files only
                        if file.stat().st_size < 10000:  # 10KB max
                            content = file.read_text(errors='replace')
                            logger.debug(f"Contents of {file}:")
                            logger.debug(content[:1000] + ("..." if len(content) > 1000 else ""))
                    except Exception as e:
                        logger.debug(f"Could not read {file}: {str(e)}")
        except Exception as e:
            logger.error(f"Error logging output directory: {str(e)}") 