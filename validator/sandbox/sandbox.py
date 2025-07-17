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
                "event": "upsert-evaluation-run",
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

            self.container = self.manager.docker.containers.run(
                image=SANDBOX_DOCKER_IMAGE,
                network=SANDBOX_NETWORK_NAME,
                volumes=volumes,
                working_dir=SANDBOX_DIR,
                environment={
                    "AI_PROXY_URL": "http://sandbox-proxy",
                    "AI_EMBEDDING_PROXY_URL": "http://sandbox-proxy"
                },
                detach=True,
            )
        except docker.errors.ImageNotFound:
            raise SystemExit(f"No docker image for {SANDBOX_DOCKER_IMAGE}. Run `./ridges.py validator run` to build the images")
        except docker.errors.APIError as e:
            if "No such image" in str(e):
                raise SystemExit(f"No docker image for {SANDBOX_DOCKER_IMAGE}. Run `./ridges.py validator run` to build the images")
            raise
        
        # Monitor container
        await self._monitor_container()
        
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
        
        return repo_path
    
    @tracer.wrap(resource="monitor-container")
    async def _monitor_container(self) -> None:
        """Monitor container execution with resource limits"""
        while True:
            if self._cancelled.is_set():
                self.container.kill()
                raise asyncio.CancelledError()
            
            try:
                self.container.reload()
                if self.container.status == "exited":
                    break
                elif self.container.status in ["dead", "removing"]:
                    break
                
                # Check memory usage
                try:
                    stats = self.container.stats(stream=False)
                    memory_stats = stats.get("memory_stats", {})
                    
                    # Try different memory usage keys
                    for key in ["usage", "current", "memory.current"]:
                        if key in memory_stats:
                            usage_mb = memory_stats[key] / (1024 * 1024)
                            if usage_mb > SANDBOX_MAX_RAM_USAGE:
                                self.container.kill()
                                raise MemoryError(f"RAM limit exceeded: {usage_mb:.1f}MB")
                            break
                except Exception:
                    pass  # Continue monitoring even if stats fail
                
                # Check runtime limit
                runtime = (datetime.now(timezone.utc) - self.evaluation_run.sandbox_created_at).total_seconds()
                if runtime > SANDBOX_MAX_RUNTIME:
                    self.container.kill()
                    raise TimeoutError(f"Runtime limit exceeded: {runtime:.1f}s")
                
            except Exception as e:
                if "exited" in str(e).lower():
                    break
                logger.warning(f"Container monitoring error: {e}")
            
            await asyncio.sleep(1)
        
        # Clean up container
        try:
            self.container.remove()
        except Exception:
            pass
    
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
            try:
                self.container.remove()
            except Exception:
                pass
        
        if self.repo_dir and self.repo_dir.exists():
            try:
                shutil.rmtree(self.repo_dir, ignore_errors=True)
            except Exception:
                pass
    
    @tracer.wrap(resource="cancel-sandbox")
    async def cancel(self) -> None:
        """Cancel sandbox execution"""
        self._cancelled.set()
