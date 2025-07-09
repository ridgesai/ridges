import asyncio
import json
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict, Any

from docker.models.containers import Container
from swebench.harness.docker_build import build_env_images
from swebench.harness.run_evaluation import load_swebench_dataset, make_test_spec, run_instance

from validator.sandbox.clone_repo import clone_repo
from validator.sandbox.constants import (
    MAIN_FILE, REPOS_BASE_DIR, REPO_CACHE_DIR, SANDBOX_DIR, SANDBOX_DOCKER_IMAGE,
    SANDBOX_INPUT_FILE, SANDBOX_MAIN_FILE, SANDBOX_NETWORK_NAME, SANDBOX_OUTPUT_FILE,
    SANDBOX_REPO_DIR, SANDBOX_SOURCE_DIR, SANDBOX_MAX_RAM_USAGE, SANDBOX_MAX_RUNTIME
)
from validator.sandbox.schema import EvaluationRun, SandboxState
from validator.utils.logging import get_logger

if TYPE_CHECKING:
    from validator.sandbox.manager import SandboxManager

logger = get_logger(__name__)

class Sandbox:
    """Async sandbox for running agent evaluations with built-in resource monitoring"""
    
    def __init__(self, evaluation_run: EvaluationRun, agent_dir: Path, manager: "SandboxManager"):
        self.evaluation_run = evaluation_run
        self.agent_dir = agent_dir.absolute()
        self.manager = manager
        self.state = SandboxState.CREATED
        self.container: Optional[Container] = None
        self.repo_dir: Optional[Path] = None
        self.start_time: Optional[float] = None
        self._cancelled = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        
        # Validate agent directory on creation
        self._validate_agent_dir()
    
    def _validate_agent_dir(self) -> None:
        """Validate agent directory structure"""
        if not self.agent_dir.exists():
            raise FileNotFoundError(f"Agent directory not found: {self.agent_dir}")
        
        agent_file = self.agent_dir / "agent.py"
        if not agent_file.exists():
            raise FileNotFoundError("agent.py not found in agent directory")
        
        # Basic validation of agent_main function
        try:
            import ast
            with open(agent_file) as f:
                tree = ast.parse(f.read())
            
            has_agent_main = any(
                isinstance(node, ast.FunctionDef) and 
                node.name == "agent_main" and 
                len(node.args.args) == 1
                for node in ast.walk(tree)
            )
            
            if not has_agent_main:
                raise ValueError("agent_main() function not found or has wrong signature")
                
        except Exception as e:
            raise ValueError(f"Failed to validate agent.py: {e}")
    
    async def run(self, evaluation_run_data: Dict[str, Any]) -> None:
        """Run the complete sandbox evaluation pipeline"""
        self.start_time = time.time()
        
        try:
            await self._transition_to(SandboxState.PATCH_GENERATING)
            await self._generate_patch(evaluation_run_data)
            
            if self.evaluation_run.response:
                await self._transition_to(SandboxState.EVALUATING)
                await self._evaluate_patch()
            
            await self._transition_to(SandboxState.COMPLETED)
            
        except asyncio.CancelledError:
            await self._transition_to(SandboxState.CANCELLED)
            raise
        except Exception as e:
            await self._transition_to(SandboxState.FAILED, str(e))
            raise
    
    async def _transition_to(self, new_state: SandboxState, error: Optional[str] = None) -> None:
        """Transition to new state and notify manager"""
        self.state = new_state
        now = datetime.now(timezone.utc)
        
        # Update evaluation run based on state
        if new_state == SandboxState.PATCH_GENERATING:
            self.evaluation_run.status = "sandbox_created"
            self.evaluation_run.sandbox_created_at = now
        elif new_state == SandboxState.EVALUATING:
            self.evaluation_run.status = "eval_started"
            self.evaluation_run.eval_started_at = now
        elif new_state in [SandboxState.COMPLETED, SandboxState.FAILED, SandboxState.CANCELLED]:
            self.evaluation_run.status = "result_scored"
            self.evaluation_run.result_scored_at = now
            if error:
                self.evaluation_run.error = error
        
        # Notify manager
        await self.manager.websocket_app.send({
            "event": "upsert-evaluation-run",
            "evaluation_run": self.evaluation_run.to_dict(),
        })
    
    async def _generate_patch(self, evaluation_run_data: Dict[str, Any]) -> None:
        """Generate patch using agent code"""
        # Setup repository
        repo_name = evaluation_run_data.get("repo")
        base_commit = evaluation_run_data.get("base_commit")
        self.repo_dir = await self._setup_repository(repo_name, base_commit)
        
        # Create input/output files
        input_file = self.agent_dir / "input.json"
        output_file = self.agent_dir / "output.json"
        
        with open(input_file, "w") as f:
            json.dump(evaluation_run_data, f)
        output_file.touch()
        
        # Run container with resource monitoring
        await self._run_container_with_monitoring(input_file, output_file)
        
        # Process results
        await self._process_patch_results(output_file)
    
    async def _setup_repository(self, repo_name: str, base_commit: str) -> Path:
        """Setup repository from cache or clone"""
        cache_key = f"{repo_name.replace('/', '_')}_{base_commit}"
        cache_path = REPO_CACHE_DIR / cache_key
        repo_path = REPOS_BASE_DIR / self.evaluation_run.run_id
        
        # Ensure directories exist
        REPO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Clone to cache if needed (run in thread to avoid blocking)
        if not cache_path.exists():
            await asyncio.get_event_loop().run_in_executor(
                None, clone_repo, cache_path, repo_name, base_commit
            )
        
        # Copy from cache
        if repo_path.exists():
            shutil.rmtree(repo_path, ignore_errors=True)
        shutil.copytree(cache_path, repo_path)
        
        return repo_path
    
    async def _run_container_with_monitoring(self, input_file: Path, output_file: Path) -> None:
        """Run container with built-in resource monitoring"""
        self.container = self.manager.docker.containers.run(
            image=SANDBOX_DOCKER_IMAGE,
            network=SANDBOX_NETWORK_NAME,
            volumes={
                str(MAIN_FILE): {"bind": SANDBOX_MAIN_FILE, "mode": "ro"},
                str(input_file): {"bind": SANDBOX_INPUT_FILE, "mode": "ro"},
                str(output_file): {"bind": SANDBOX_OUTPUT_FILE, "mode": "rw"},
                str(self.agent_dir): {"bind": SANDBOX_SOURCE_DIR, "mode": "ro"},
                str(self.repo_dir): {"bind": SANDBOX_REPO_DIR, "mode": "rw"},
            },
            working_dir=SANDBOX_DIR,
            environment={
                "AI_PROXY_URL": "http://sandbox-proxy",
                "AI_EMBEDDING_PROXY_URL": "http://sandbox-proxy"
            },
            detach=True,
        )
        
        # Monitor container with resource limits
        try:
            await self._monitor_container()
        finally:
            if self.container:
                try:
                    self.container.remove()
                except:
                    pass
    
    async def _monitor_container(self) -> None:
        """Monitor container execution with resource limits"""
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while True:
            if self._cancelled.is_set():
                try:
                    self.container.kill()
                except Exception as e:
                    logger.warning(f"Error killing container during cancellation: {e}")
                raise asyncio.CancelledError()
            
            try:
                self.container.reload()
                if self.container.status == "exited":
                    logger.debug(f"Container exited with status: {self.container.status}")
                    break
                elif self.container.status in ["dead", "removing"]:
                    logger.warning(f"Container in unexpected state: {self.container.status}")
                    break
                
                # Reset error counter on successful container check
                consecutive_errors = 0
                
            except Exception as e:
                consecutive_errors += 1
                logger.warning(f"Error reloading container (attempt {consecutive_errors}/{max_consecutive_errors}): {e}")
                
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many consecutive container errors, assuming container is dead")
                    break
                
                # Continue to runtime check even if container reload fails
            
            # Check resource usage with robust error handling
            try:
                stats = self.container.stats(stream=False)
                ram_usage = self._extract_memory_usage(stats)
                
                if ram_usage and ram_usage > SANDBOX_MAX_RAM_USAGE:
                    logger.warning(f"RAM limit exceeded: {ram_usage:.1f}MB > {SANDBOX_MAX_RAM_USAGE}MB")
                    self.container.kill()
                    raise MemoryError(f"RAM limit exceeded: {ram_usage:.1f}MB")
                    
            except Exception as e:
                logger.warning(f"Error getting container stats: {e}")
                # Continue monitoring even if stats fail - runtime limits still apply
            
            # Check runtime limit
            runtime = time.time() - self.start_time
            if runtime > SANDBOX_MAX_RUNTIME:
                logger.warning(f"Runtime limit exceeded: {runtime:.1f}s > {SANDBOX_MAX_RUNTIME}s")
                try:
                    self.container.kill()
                except Exception as e:
                    logger.warning(f"Error killing container during timeout: {e}")
                raise TimeoutError(f"Runtime limit exceeded: {runtime:.1f}s")
            
            await asyncio.sleep(1)
    
    def _extract_memory_usage(self, stats: dict) -> Optional[float]:
        """Extract memory usage from Docker stats with fallback logic"""
        try:
            memory_stats = stats.get("memory_stats", {})
            
            if not memory_stats:
                logger.debug("No memory_stats found in Docker stats")
                return None
            
            # Try different possible memory usage keys (cgroup v1 vs v2)
            usage_keys = [
                "usage",           # cgroup v1
                "current",         # cgroup v2
                "memory.current",  # alternative cgroup v2
            ]
            
            for key in usage_keys:
                if key in memory_stats:
                    usage_bytes = memory_stats[key]
                    if isinstance(usage_bytes, (int, float)) and usage_bytes > 0:
                        logger.debug(f"Found memory usage via key '{key}': {usage_bytes / (1024 * 1024):.1f}MB")
                        return usage_bytes / (1024 * 1024)  # Convert to MB
            
            # Log available keys for debugging
            available_keys = list(memory_stats.keys())
            logger.debug(f"Memory stats keys available: {available_keys}")
            
            # Fallback: try to find any numeric value that looks like memory usage
            for key, value in memory_stats.items():
                if isinstance(value, (int, float)) and value > 1024 * 1024:  # At least 1MB
                    logger.debug(f"Using fallback memory key '{key}' with value {value / (1024 * 1024):.1f}MB")
                    return value / (1024 * 1024)
                    
            logger.debug(f"No usable memory usage found in stats with keys: {available_keys}")
            return None
            
        except Exception as e:
            logger.warning(f"Error extracting memory usage: {e}")
            return None
    
    async def _process_patch_results(self, output_file: Path) -> None:
        """Process patch generation results"""
        try:
            with open(output_file) as f:
                result = json.load(f)
            
            if result.get("success"):
                patch = result.get("output", {}).get("patch", "")
                if patch:
                    self.evaluation_run.response = patch
                    self.evaluation_run.status = "patch_generated"
                    self.evaluation_run.patch_generated_at = datetime.now(timezone.utc)
                else:
                    raise ValueError("Empty patch returned from agent")
            else:
                raise ValueError(result.get("error", "Unknown error"))
                
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse agent output: {e}")
    
    async def _evaluate_patch(self) -> None:
        """Evaluate patch using SWE-bench"""
        # First check if the patch applies properly
        logger.info(f"Testing patch application for {self.evaluation_run.swebench_instance_id}")
        patch_error = self._get_patch_apply_error()
        
        if patch_error:
            error_msg = f"Patch failed to apply: {patch_error}"
            logger.error(f"Patch application failed for {self.evaluation_run.swebench_instance_id}: {patch_error}")
            self.evaluation_run.error = error_msg
            self.evaluation_run.solved = False
            return
        
        logger.info(f"Patch applies successfully, running SWE-bench evaluation for {self.evaluation_run.swebench_instance_id}")
        
        # Run evaluation in thread pool to avoid blocking
        await asyncio.get_event_loop().run_in_executor(None, self._run_swebench_evaluation)
    
    def _run_swebench_evaluation(self) -> None:
        """Run SWE-bench evaluation (blocking operation)"""
        instance_id = self.evaluation_run.swebench_instance_id
        logger.info(f"Starting SWE-bench evaluation for instance {instance_id}")
        
        try:
            # Load SWE-bench instance
            instance = load_swebench_dataset(
                "SWE-bench/SWE-bench_Verified", "test", [instance_id]
            )[0]
            
            # Create prediction and test spec
            prediction = {
                "instance_id": instance_id,
                "model_name_or_path": self.evaluation_run.run_id,
                "model_patch": self.evaluation_run.response,
            }
            test_spec = make_test_spec(instance)
            
            # Build environment and run evaluation
            logger.info(f"Building Docker environment for instance {instance_id}")
            build_env_images(self.manager.docker, [test_spec], max_workers=4)
            
            logger.info(f"Running SWE-bench evaluation for instance {instance_id}")
            result = run_instance(
                test_spec=test_spec,
                pred=prediction,
                rm_image=False,
                force_rebuild=False,
                client=self.manager.docker,
                run_id=self.evaluation_run.run_id,
                timeout=1800,
                rewrite_reports=False,
            )
            
            self._process_evaluation_results(result)
            
        except Exception as e:
            error_msg = f"SWE-bench evaluation failed for {instance_id}: {str(e)}"
            logger.error(error_msg)
            logger.exception("Full traceback:")
            self.evaluation_run.error = error_msg
            self.evaluation_run.solved = False
    
    def _process_evaluation_results(self, result) -> None:
        """Process SWE-bench evaluation results"""
        instance_id = self.evaluation_run.swebench_instance_id
        
        if not result:
            logger.info(f"SWE-bench evaluation returned no results for {instance_id} - patch didn't fix any tests")
            self.evaluation_run.solved = False
            # Set empty test results to indicate no tests were affected
            self.evaluation_run.fail_to_pass_success = json.dumps([])
            self.evaluation_run.pass_to_pass_success = json.dumps([])
            self.evaluation_run.fail_to_fail_success = json.dumps([])
            self.evaluation_run.pass_to_fail_success = json.dumps([])
            return
            
        try:
            result_instance_id, report = result
            report = report[instance_id]
            
            if "tests_status" in report:
                tests = report["tests_status"]
                self.evaluation_run.fail_to_pass_success = json.dumps(tests["FAIL_TO_PASS"]["success"])
                self.evaluation_run.pass_to_pass_success = json.dumps(tests["PASS_TO_PASS"]["success"])
                self.evaluation_run.fail_to_fail_success = json.dumps(tests["FAIL_TO_FAIL"]["success"])
                self.evaluation_run.pass_to_fail_success = json.dumps(tests["PASS_TO_FAIL"]["success"])
                self.evaluation_run.solved = report.get("resolved", False)
                
                logger.info(f"Evaluation completed for {instance_id}: resolved={self.evaluation_run.solved}")
                
                # Log test results for debugging if not resolved
                if not self.evaluation_run.solved:
                    logger.info(f"Test results for {instance_id}:")
                    for test_type, test_data in tests.items():
                        success_count = len(test_data.get("success", []))
                        total_count = len(test_data.get("success", [])) + len(test_data.get("failure", []))
                        logger.info(f"  {test_type}: {success_count}/{total_count} passed")
            else:
                error_msg = f"No test results found in evaluation report"
                logger.error(error_msg)
                self.evaluation_run.solved = False
                self.evaluation_run.error = error_msg
                
        except Exception as e:
            error_msg = f"Failed to process evaluation results: {str(e)}"
            logger.error(error_msg)
            logger.exception("Full traceback:")
            self.evaluation_run.solved = False
            self.evaluation_run.error = error_msg
    
    async def cancel(self) -> None:
        """Cancel sandbox execution"""
        self._cancelled.set()
        if self.container:
            try:
                self.container.kill()
            except:
                pass
    
    def cleanup(self) -> None:
        """Clean up sandbox resources"""
        if self.container:
            try:
                self.container.remove()
            except:
                pass
        
        if self.repo_dir and self.repo_dir.exists():
            try:
                shutil.rmtree(self.repo_dir, ignore_errors=True)
            except:
                pass

    def _get_patch_apply_error(self) -> str | None:
        """Test if the patch applies and return detailed error if it doesn't"""
        if not self.evaluation_run.response or not self.evaluation_run.response.strip():
            return "Patch is empty or None"
        
        patch_path = Path(tempfile.mkstemp(suffix=".patch")[1])
        patch_path.write_text(self.evaluation_run.response)
        branch = f"patch-test-{uuid.uuid4().hex[:8]}"
        
        logger.debug(f"Testing patch application in {self.repo_dir}")
        logger.debug(f"Patch content preview: {self.evaluation_run.response[:200]}...")
        
        try:
            # Get current commit
            current_commit_hash = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_dir,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout.decode().strip()
            
            logger.debug(f"Current commit: {current_commit_hash}")
            
            # Create test branch
            subprocess.run(
                ["git", "checkout", "-b", branch, current_commit_hash],
                cwd=self.repo_dir,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            
            logger.debug(f"Created test branch: {branch}")
            
            # Try to apply patch
            result = subprocess.run(
                [
                    "git",
                    "apply",
                    "--verbose",
                    "--reject",
                    "--unidiff-zero",
                    str(patch_path),
                ],
                cwd=self.repo_dir,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            
            logger.debug(f"Git apply result: returncode={result.returncode}, output={result.stdout}")
            
            # Return None if patch applied successfully (returncode == 0)
            # Return error details if patch failed (returncode != 0)
            if result.returncode == 0:
                logger.debug("Patch applied successfully")
                return None
            else:
                error_output = result.stdout.strip()
                logger.debug(f"Patch application failed: {error_output}")
                return error_output if error_output else f"Git apply failed with exit code {result.returncode}"
                
        except subprocess.CalledProcessError as e:
            error_msg = "Git patch apply error: " + (e.stderr.decode().strip() if e.stderr else str(e))
            logger.debug(f"Exception during patch application: {error_msg}")
            return error_msg
        finally:
            # Clean up: checkout back to original branch and delete test branch
            try:
                subprocess.run(
                    ["git", "checkout", "-"],
                    cwd=self.repo_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.run(
                    ["git", "branch", "-D", branch],
                    cwd=self.repo_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except:
                pass  # Ignore cleanup errors
            
            patch_path.unlink(missing_ok=True)
