import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict, Any
from ddtrace import tracer

import docker
from docker.errors import ImageNotFound, APIError
from docker.models.containers import Container
from swebench.harness.docker_build import build_env_images
from swebench.harness.run_evaluation import load_swebench_dataset, make_test_spec, run_instance

from validator.sandbox.clone_repo import clone_repo
from validator.sandbox.constants import (
    MAIN_FILE, REPOS_BASE_DIR, REPO_CACHE_DIR, SANDBOX_DIR, SANDBOX_DOCKER_IMAGE,
    SANDBOX_INPUT_FILE, SANDBOX_MAIN_FILE, SANDBOX_NETWORK_NAME, SANDBOX_OUTPUT_FILE,
    SANDBOX_REPO_DIR, SANDBOX_SOURCE_DIR, SANDBOX_MAX_RAM_USAGE, SANDBOX_MAX_RUNTIME
)
from validator.sandbox.schema import EvaluationRun, SandboxInput, SwebenchProblem
from loggers.logging_utils import get_logger

if TYPE_CHECKING:
    from validator.sandbox.manager import SandboxManager

logger = get_logger(__name__)

PRE_EMBEDDED_MOUNT = '/pre_embedded/chunks.json.gz'

def get_sandbox_image_for_instance(instance_id: str) -> str:
    """Get commit-specific Docker image for a SWE-bench instance."""
    # TEMPORARILY: Use default image until commit-specific images are fixed for AMD64
    logger.info(f"Using default sandbox image for {instance_id} (commit-specific images have architecture issues)")
    return SANDBOX_DOCKER_IMAGE

class Sandbox:
    """Async sandbox for running agent evaluations"""
    
    @tracer.wrap(resource="initialize-sandbox")
    def __init__(self, evaluation_run: EvaluationRun, problem: SwebenchProblem, agent_dir: Path, manager: "SandboxManager"):
        self.evaluation_run = evaluation_run
        self.problem = problem
        self.agent_dir = agent_dir.absolute()
        self.manager = manager
        self.container: Optional[Container] = None
        self.repo_dir: Optional[Path] = None
        self._cancelled = False
        
        # Validate agent directory
        if not (agent_dir / "agent.py").exists():
            raise FileNotFoundError("agent.py not found in agent directory")
    
    def cancel(self) -> None:
        """Mark this sandbox as cancelled to stop websocket communications."""
        self._cancelled = True

    @tracer.wrap(resource="run-sandbox")
    async def run(self) -> None:
        """Run the complete sandbox evaluation pipeline"""
        
        try:
            # Generate patch (logs captured automatically)
            await self._generate_patch()
            
            # Evaluate patch if generated
            if self.evaluation_run.response:
                self.evaluation_run.status = "eval_started"
                self.evaluation_run.eval_started_at = datetime.now(timezone.utc)
                await self._send_update()
                await self._evaluate_patch()
            
            # Mark completed
            self.evaluation_run.status = "result_scored"
            self.evaluation_run.result_scored_at = datetime.now(timezone.utc)
            await self._send_update()
            
        except Exception as e:
            logger.error(f"Sandbox {self.evaluation_run.run_id} failed: {e}")
            self.evaluation_run.error = str(e)
            self.evaluation_run.status = "result_scored"
            self.evaluation_run.result_scored_at = datetime.now(timezone.utc)
            self.evaluation_run.solved = False
            await self._send_update()
    
    @tracer.wrap(resource="send-update")
    async def _send_update(self) -> None:
        """Send evaluation run update"""
        if self._cancelled:
            return
        try:
            await self.manager.websocket_app.send({
                "event": "update-evaluation-run",
                "evaluation_run": self.evaluation_run.to_dict(),
            })
        except Exception as e:
            logger.error(f"Failed to send update: {e}")
    
    @tracer.wrap(resource="generate-patch")
    async def _generate_patch(self) -> None:
        """Generate patch using agent code"""
        # Setup repository
        repo_name = self.problem.repo
        base_commit = self.problem.base_commit
        self.repo_dir = await self._setup_repository(repo_name, base_commit)
        
        # Create input/output files
        io_dir = self.agent_dir / f"io-{self.evaluation_run.run_id}"
        io_dir.mkdir(parents=True, exist_ok=True)
        input_file = io_dir / "input.json"
        output_file = io_dir / "output.json"

        input = SandboxInput(
            instance_id=self.problem.instance_id,
            problem_statement=self.problem.problem_statement,
            repo=self.problem.repo,
            base_commit=self.problem.base_commit,
            run_id=self.evaluation_run.run_id,
        )
        
        input_file.write_text(input.model_dump_json())
        output_file.touch()
        
        # Run container
        try:
            volumes = {
                str(MAIN_FILE): {"bind": SANDBOX_MAIN_FILE, "mode": "ro"},
                str(input_file): {"bind": SANDBOX_INPUT_FILE, "mode": "ro"},
                str(output_file): {"bind": SANDBOX_OUTPUT_FILE, "mode": "rw"},
                str(self.agent_dir): {"bind": SANDBOX_SOURCE_DIR, "mode": "ro"},
                str(self.repo_dir): {"bind": SANDBOX_REPO_DIR, "mode": "rw"},
            }
            # Mount pre-embedded file if exists
            embed_file = Path(__file__).parent.parent / 'repo_embeds' / f'{self.evaluation_run.swebench_instance_id}.json.gz'
            if embed_file.exists():
                volumes[str(embed_file)] = {'bind': PRE_EMBEDDED_MOUNT, 'mode': 'ro'}

            # Get image name and always pull the latest version from GHCR
            image_name = get_sandbox_image_for_instance(self.evaluation_run.swebench_instance_id)
            logger.info(f"Pulling latest version of image: {image_name}")
            try:
                self.manager.docker.images.pull(image_name)
                logger.info(f"Successfully pulled image: {image_name}")
            except Exception as e:
                logger.warning(f"Failed to pull image {image_name}: {e}")
                # For default sandbox image, this should not fail
                if image_name == SANDBOX_DOCKER_IMAGE:
                    raise SystemExit(f"Failed to pull default sandbox image {SANDBOX_DOCKER_IMAGE}: {e}")

            self.container = self.manager.docker.containers.run(
                remove=False,  # Keep container for log capture, remove manually later
                image=image_name,
                network=SANDBOX_NETWORK_NAME,
                volumes=volumes,
                working_dir=SANDBOX_DIR,
                environment={
                    "AI_PROXY_URL": "http://sandbox-proxy",
                    "AI_EMBEDDING_PROXY_URL": "http://sandbox-proxy",
                    "PYTHONUNBUFFERED": "1"  # Ensure Python output is not buffered
                },
                user=f"{os.getuid()}:{os.getgid()}",  # Run container as host user:group
                detach=True,
                # Add CPU and memory limits to prevent resource exhaustion
                mem_limit=f"{SANDBOX_MAX_RAM_USAGE}m",
            )

            self.manager._track_container(self.container)
            
        except ImageNotFound:
            raise SystemExit(f"No docker image for {SANDBOX_DOCKER_IMAGE}. Run `./ridges.py validator run` to build the images")
        except APIError as e:
            if "No such image" in str(e):
                raise SystemExit(f"No docker image for {SANDBOX_DOCKER_IMAGE}. Run `./ridges.py validator run` to build the images")
            raise
        
        # Wait for container to complete and capture logs
        await self._wait_for_container_and_capture_logs()
        
        # Process results
        try:

            logger.info(f"Checking output file: {output_file}")
            logger.info(f"Output file exists: {output_file.exists()}")
            if output_file.exists():
                logger.info(f"Output file size: {output_file.stat().st_size} bytes")
            
            text = output_file.read_text()
            logger.info(f"Raw agent output: {repr(text[:500])}{'...' if len(text) > 500 else ''}")
            
            if not text.strip():
                raise ValueError("Agent produced empty output")
                
            result = json.loads(text)
            logger.info(f"Parsed agent result keys: {list(result.keys())}")
            
            if result.get("success"):
                output = result.get("output", {})
                logger.info(f"Output section keys: {list(output.keys()) if isinstance(output, dict) else type(output)}")
                patch = str(output.get("patch", "")) if isinstance(output, dict) else ""
                if patch:
                    self.evaluation_run.response = patch
                    self.evaluation_run.status = "patch_generated"
                    self.evaluation_run.patch_generated_at = datetime.now(timezone.utc)
                    # Send all logs as a single batch now that patch is generated
                else:
                    raise ValueError(f"Empty patch returned from agent. Output: {output}")
            else:
                raise ValueError(result.get("error", "Unknown error"))
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            logger.error(f"Full agent output text: {repr(text)}")
            raise ValueError(f"Failed to parse agent output: {e}. Agent output: {text}")
        except Exception as e:
            raise
        finally:
            # Clean up IO directory after processing results
            shutil.rmtree(io_dir, ignore_errors=True)
    
    @tracer.wrap(resource="setup-repository")
    async def _setup_repository(self, repo_name: str, base_commit: str) -> Path:
        """Setup repository from cache or clone"""
        cache_key = f"{repo_name.replace('/', '_')}_{base_commit}"
        cache_path = REPO_CACHE_DIR / cache_key
        repo_path = REPOS_BASE_DIR / self.evaluation_run.run_id
        
        REPO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Clone to cache if needed
        await asyncio.get_event_loop().run_in_executor(
                None, clone_repo, cache_path, repo_name, base_commit
            )
        
        # Copy from cache
        if repo_path.exists():
            shutil.rmtree(repo_path, ignore_errors=True)
        shutil.copytree(cache_path, repo_path)

        try:
            from swebench.harness.run_evaluation import load_swebench_dataset  # local import to avoid at module load

            instance_id = self.evaluation_run.swebench_instance_id
            instance = load_swebench_dataset(
                "SWE-bench/SWE-bench_Verified",
                "test",
                [instance_id],
            )[0]

            test_patch = instance.get("test_patch")
            if test_patch:
                # First, get the list of files that will be affected by the patch
                affected_files = []
                for line in test_patch.split('\n'):
                    if line.startswith('--- a/') or line.startswith('+++ b/'):
                        # Extract filename from patch header
                        if line.startswith('--- a/'):
                            filename = line[6:]  # Remove '--- a/' prefix
                        else:  # '+++ b/'
                            filename = line[6:]  # Remove '+++ b/' prefix
                        if filename != '/dev/null' and filename not in affected_files:
                            affected_files.append(filename)
                
                proc = subprocess.run(
                    ["git", "apply", "--verbose", "--reject", "--unidiff-zero", "-"],
                    cwd=repo_path,
                    input=test_patch,
                    text=True,
                    capture_output=True,
                )
                if proc.returncode != 0:
                    logger.error(
                        "Failed to apply test_patch for %s: %s %s",
                        instance_id,
                        proc.stdout,
                        proc.stderr,
                    )
                    raise RuntimeError(
                        f"Failed to apply test_patch for {instance_id}. See logs for details."
                    )
                else:
                    logger.info("Successfully applied test_patch for %s", instance_id)
                    logger.info("Affected files: %s", affected_files)
                    try:
                        # Only add the specific files that were modified by the patch
                        if affected_files:
                            subprocess.run(["git", "add"] + affected_files, cwd=repo_path, check=True)
                        subprocess.run([
                            "git",
                            "-c",
                            "user.email=tao@localhost",
                            "-c",
                            "user.name=Tao God",
                            "commit",
                            "-m",
                            "updates",
                        ], cwd=repo_path, check=True)
                        logger.info("Committed test_patch for %s", instance_id)
                    except Exception as ce:
                        logger.warning("Could not commit test_patch for %s: %s", instance_id, ce)
        except Exception as e:
            # Reraise to fail early – the sandbox should not continue with an incomplete test suite
            raise
        
        return repo_path
    
    @tracer.wrap(resource="wait-container-capture-logs")
    async def _wait_for_container_and_capture_logs(self) -> None:
        """Wait for container completion and capture logs via docker logs command"""
        run_id = self.evaluation_run.run_id
        
        # Monitor container status until completion
        container_start_time = datetime.now(timezone.utc)
        
        try:
            while True:
                if self._cancelled:
                    logger.info(f"Container cancelled for {run_id}")
                    self.container.kill()
                    break
                
                # Check container status
                try:
                    self.container.reload()
                    status = self.container.status
                    
                    if status in ["exited", "dead", "removing"]:
                        try:
                            exit_code = self.container.attrs.get('State', {}).get('ExitCode', 'unknown')
                            logger.info(f"Container {run_id} status: {status}, exit code: {exit_code} - completed")
                        except:
                            logger.info(f"Container {run_id} status: {status} - completed")
                        break
                        
                except Exception as e:
                    logger.warning(f"Container status check failed for {run_id}: {e}")
                    break
                
                # Check runtime limit
                runtime = (datetime.now(timezone.utc) - container_start_time).total_seconds()
                if runtime > SANDBOX_MAX_RUNTIME:
                    logger.error(f"Container {run_id} exceeded runtime limit: {runtime:.1f}s")
                    try:
                        self.container.stop(timeout=5)
                    except:
                        self.container.kill()
                    break
                
                await asyncio.sleep(1)
            
            # Capture logs after container completion
            await self._capture_container_logs_post_execution()
                    
        except Exception as e:
            logger.error(f"Error in container monitoring for {run_id}: {e}")
            
    @tracer.wrap(resource="capture-logs-post-execution")
    async def _capture_container_logs_post_execution(self) -> None:
        """Capture complete container logs after execution using docker logs"""
        run_id = self.evaluation_run.run_id
        
        try:
            logger.info(f"📋 Capturing logs for container {run_id}...")
            
            # Get complete logs from container
            logs_bytes = self.container.logs(
                stdout=True,
                stderr=True,
                timestamps=False
            )
            
            # Decode logs to string
            container_logs = logs_bytes.decode('utf-8', errors='ignore')
            
            logger.info(f"✅ Captured {len(container_logs)} characters of logs for {run_id}")
            
            # Send logs seperately (so we don't have to send them every time we update)
            try:
                await self.manager.websocket_app.send({
                    "event": "evaluation-run-logs",
                    "run_id": str(run_id),
                    "logs": container_logs  # Send complete logs, no truncation
                })
                logger.info(f"📤 Sent complete logs via websocket for {run_id}")
            except Exception as e:
                logger.warning(f"Failed to send logs via websocket for {run_id}: {e}")
                
        except Exception as e:
            logger.error(f"❌ Error capturing logs for {run_id}: {e}")
            # Set error message as logs to avoid None
            self.evaluation_run.logs = f"Error capturing logs: {e}"
        
        finally:
            # Clean up container after log capture
            try:
                logger.info(f"🧹 Cleaning up container {run_id}")
                self.container.remove(force=True)
                logger.info(f"✅ Container {run_id} removed successfully")
            except Exception as e:
                logger.warning(f"Failed to remove container {self.container.id}: {e}")
    
    @tracer.wrap(resource="evaluate-patch")
    async def _evaluate_patch(self) -> None:
        """Evaluate patch using SWE-bench"""
        # Check if patch applies
        patch_error = self._check_patch_applies()
        if patch_error:
            logger.error(f"Patch application failed: {patch_error}")
            self.evaluation_run.error = f"Patch failed to apply: {patch_error}"
            self.evaluation_run.solved = False
            return
        
        # Run SWE-bench evaluation
        await asyncio.get_event_loop().run_in_executor(None, self._run_swebench_evaluation)
    
    @tracer.wrap(resource="check-if-patch-applies")
    def _check_patch_applies(self) -> Optional[str]:
        """Test if the patch applies and return error if it doesn't"""
        if not self.evaluation_run.response or not self.evaluation_run.response.strip():
            return "Patch is empty or None"
        
        patch_path = Path(tempfile.mkstemp(suffix=".patch")[1])
        patch_path.write_text(self.evaluation_run.response)
        branch = f"patch-test-{uuid.uuid4().hex[:8]}"
        
        try:
            # Create test branch
            subprocess.run(
                ["git", "checkout", "-b", branch],
                cwd=self.repo_dir, check=True, capture_output=True
            )
            
            # Try to apply patch
            result = subprocess.run(
                ["git", "apply", "--verbose", "--reject", "--unidiff-zero", str(patch_path)],
                cwd=self.repo_dir, capture_output=True, text=True
            )
            
            return None if result.returncode == 0 else result.stdout.strip()
            
        except subprocess.CalledProcessError as e:
            return f"Git patch apply error: {e.stderr.decode().strip() if e.stderr else str(e)}"
        finally:
            # Cleanup
            try:
                subprocess.run(["git", "checkout", "-"], cwd=self.repo_dir, capture_output=True)
                subprocess.run(["git", "branch", "-D", branch], cwd=self.repo_dir, capture_output=True)
            except Exception:
                pass
            patch_path.unlink(missing_ok=True)
    
    @tracer.wrap(resource="run-swebench-evaluation")
    def _run_swebench_evaluation(self) -> None:
        """Run SWE-bench evaluation (blocking operation)"""
        instance_id = self.evaluation_run.swebench_instance_id
        
        try:
            # Load instance and create prediction
            instance = load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test", [instance_id])[0]
            prediction = {
                "instance_id": instance_id,
                "model_name_or_path": self.evaluation_run.run_id,
                "model_patch": self.evaluation_run.response,
            }
            test_spec = make_test_spec(instance)
            
            # Build environment and run evaluation
            build_env_images(self.manager.docker, [test_spec], max_workers=4)
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
            
            # Process results
            if result:
                _, report = result
                report = report[instance_id]
                
                if "tests_status" in report:
                    tests = report["tests_status"]
                    self.evaluation_run.fail_to_pass_success = json.dumps(tests["FAIL_TO_PASS"]["success"])
                    self.evaluation_run.pass_to_pass_success = json.dumps(tests["PASS_TO_PASS"]["success"])
                    self.evaluation_run.fail_to_fail_success = json.dumps(tests["FAIL_TO_FAIL"]["success"])
                    self.evaluation_run.pass_to_fail_success = json.dumps(tests["PASS_TO_FAIL"]["success"])
                    self.evaluation_run.solved = report.get("resolved", False)
                else:
                    self.evaluation_run.solved = False
                    self.evaluation_run.error = "No test results found in evaluation report"
            else:
                # No results means patch didn't fix any tests
                self.evaluation_run.solved = False
                self.evaluation_run.fail_to_pass_success = json.dumps([])
                self.evaluation_run.pass_to_pass_success = json.dumps([])
                self.evaluation_run.fail_to_fail_success = json.dumps([])
                self.evaluation_run.pass_to_fail_success = json.dumps([])
                
        except Exception as e:
            logger.error(f"SWE-bench evaluation failed: {e}")
            self.evaluation_run.error = f"SWE-bench evaluation failed: {str(e)}"
            self.evaluation_run.solved = False
    
    @tracer.wrap(resource="cleanup-sandbox")
    def cleanup(self) -> None:
        """Clean up sandbox resources"""
        if self.container:
            self.manager._untrack_container(self.container)
            
        if self.container:
            try:
                self.container.remove(force=True)
                logger.info(f"Force removed sandbox container {self.container.id}")
            except Exception as e:
                logger.warning(f"Failed to remove container {self.container.id}: {e}")
                try:
                    subprocess.run(["docker", "rm", "-f", self.container.id], timeout=10)
                    logger.info(f"Removed container {self.container.id} via CLI fallback")
                except Exception as cli_error:
                    logger.error(f"CLI fallback also failed for container {self.container.id}: {cli_error}")
        
        if self.repo_dir and self.repo_dir.exists():
            try:
                shutil.rmtree(self.repo_dir, ignore_errors=True)
                logger.info(f"Cleaned up repository directory: {self.repo_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up repository directory {self.repo_dir}: {e}")
        
        # Clean up IO directory if it exists
        io_dir = self.agent_dir / f"io-{self.evaluation_run.run_id}"
        if io_dir.exists():
            try:
                shutil.rmtree(io_dir, ignore_errors=True)
                logger.info(f"Cleaned up IO directory: {io_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up IO directory {io_dir}: {e}")
    
