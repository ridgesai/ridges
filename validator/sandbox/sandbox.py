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
from ddtrace import tracer

import docker
from docker.errors import NotFound as DockerNotFound
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
        self._cancelled = asyncio.Event()
        
        # Validate agent directory
        if not (agent_dir / "agent.py").exists():
            raise FileNotFoundError("agent.py not found in agent directory")

    @tracer.wrap(resource="run-sandbox")
    async def run(self) -> None:
        """Run the complete sandbox evaluation pipeline"""
        
        try:
            # Generate patch
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
                remove=True,
                image=image_name,
                network=SANDBOX_NETWORK_NAME,
                volumes=volumes,
                working_dir=SANDBOX_DIR,
                environment={
                    "AI_PROXY_URL": "http://sandbox-proxy",
                    "AI_EMBEDDING_PROXY_URL": "http://sandbox-proxy",
                    "PYTHONUNBUFFERED": "1"  # Ensure Python output is not buffered
                },
                detach=True,
                # Add CPU and memory limits to prevent resource exhaustion
                mem_limit=f"{SANDBOX_MAX_RAM_USAGE}m",
            )
            
        except docker.errors.ImageNotFound:
            raise SystemExit(f"No docker image for {SANDBOX_DOCKER_IMAGE}. Run `./ridges.py validator run` to build the images")
        except docker.errors.APIError as e:
            if "No such image" in str(e):
                raise SystemExit(f"No docker image for {SANDBOX_DOCKER_IMAGE}. Run `./ridges.py validator run` to build the images")
            raise
        
        # Start log streaming and monitoring in parallel
        await asyncio.gather(
            self._stream_and_monitor_container(),
            return_exceptions=True
        )
        
        # Process results
        try:
            text = output_file.read_text()
            result = json.loads(text)
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
            raise ValueError(f"Failed to parse agent output: {e}. Agent output: {text}")
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
        if not cache_path.exists():
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
                    try:
                        subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
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
    
    @tracer.wrap(resource="stream-and-monitor-container")
    async def _stream_and_monitor_container(self) -> None:
        """Stream logs and monitor container in a clean, minimal way"""
        run_id = self.evaluation_run.run_id
        logger.info(f"🚀 STARTING LOG CAPTURE FOR CONTAINER {self.container.id[:12]} (run_id: {run_id})")
        
        # Start log streaming immediately
        log_task = asyncio.create_task(self._capture_container_logs())
        
        # Monitor container status
        container_start_time = datetime.now(timezone.utc)
        
        try:
            while True:
                if self._cancelled.is_set():
                    logger.info(f"Container cancelled for {run_id}")
                    break
                
                # Check container status
                try:
                    self.container.reload()
                    status = self.container.status
                    
                    if status in ["exited", "dead", "removing"]:
                        logger.info(f"Container {run_id} status: {status} - stopping monitoring")
                        break
                        
                except DockerNotFound:
                    logger.info(f"Container {run_id} auto-removed - evaluation completed")
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
                
        finally:
            # Ensure log streaming stops
            if not log_task.done():
                log_task.cancel()
                try:
                    await log_task
                except asyncio.CancelledError:
                    pass
            
    @tracer.wrap(resource="capture-container-logs")  
    async def _capture_container_logs(self) -> None:
        """Capture all container logs in real-time - FIXED THREADING"""
        run_id = self.evaluation_run.run_id
        
        try:
            # Get the current event loop to pass to the blocking thread
            loop = asyncio.get_running_loop()
            
            await loop.run_in_executor(
                None, self._read_logs_blocking, loop
            )
            
        except Exception as e:
            logger.error(f"LOG CAPTURE ERROR for {run_id}: {e}")
    
    def _read_logs_blocking(self, loop) -> None:
        """Read logs in blocking thread - FIXED EVENT LOOP"""
        run_id = self.evaluation_run.run_id
        
        try:
            # Get real-time log stream 
            log_stream = self.container.logs(
                stream=True,
                follow=True, 
                stdout=True,
                stderr=True,
                timestamps=False
            )
            
            for raw_log_line in log_stream:
                try:
                    # Decode the log line (handle Docker's 8-byte header)
                    if len(raw_log_line) >= 8 and raw_log_line[0] in [1, 2]:
                        line = raw_log_line[8:].decode('utf-8', errors='ignore').rstrip()
                    else:
                        line = raw_log_line.decode('utf-8', errors='ignore').rstrip()
                    
                    if line.strip():  # Only process non-empty lines
                        asyncio.run_coroutine_threadsafe(self._send_log_via_websocket(line), loop)
                        
                except Exception as e:
                    logger.error(f"Error processing log line for {run_id}: {e}")
                    
                # Check if container stopped
                try:
                    self.container.reload()
                    if self.container.status not in ["running", "created"]:
                        logger.info(f"Container {run_id} stopped, ending log stream")
                        break
                except:
                    break
                    
        except Exception as e:
            logger.error(f"Error in log stream for {run_id}: {e}")
        finally:
            logger.info(f"LOG STREAM ENDED for {run_id}")
    
    @tracer.wrap(resource="send-log-via-websocket")
    async def _send_log_via_websocket(self, line: str) -> None:
        """Send log line via websocket and store in database"""
        try:
            await self.manager.websocket_app.send({
                "event": "evaluation-run-log",
                "run_id": str(self.evaluation_run.run_id),
                "line": line,
            })
        except Exception as e:
            logger.error(f"Failed to send websocket log for {self.evaluation_run.run_id}: {e}")
    
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
        # Sandbox container has --rm (remove=True) so it will be removed automatically
        if self.repo_dir and self.repo_dir.exists():
            try:
                shutil.rmtree(self.repo_dir, ignore_errors=True)
            except Exception:
                pass
    
    @tracer.wrap(resource="cancel-sandbox")
    async def cancel(self) -> None:
        """Cancel sandbox execution"""
        self._cancelled.set()
