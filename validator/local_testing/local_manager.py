"""
Local sandbox manager for testing agents without database infrastructure.
This module provides:
- LocalSandboxManager: Manages Docker containers and networks for local testing
- LocalSandbox: Modified sandbox that doesn't need websockets or database
"""
import asyncio
import tempfile
import uuid
import shutil
import json
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import os
from validator.sandbox.constants import (
    SANDBOX_DOCKER_IMAGE, PROXY_DOCKER_IMAGE, SANDBOX_NETWORK_NAME,
    PROXY_CONTAINER_NAME, SANDBOX_MAX_RAM_USAGE, SANDBOX_MAX_RUNTIME,
    MAIN_FILE, SANDBOX_MAIN_FILE, SANDBOX_INPUT_FILE, SANDBOX_OUTPUT_FILE,
    SANDBOX_SOURCE_DIR, SANDBOX_REPO_DIR, SANDBOX_DIR
)
from validator.sandbox.schema import EvaluationRun, SwebenchProblem, SandboxInput
from validator.sandbox.sandbox import Sandbox
from validator.sandbox.clone_repo import clone_repo
from loggers.logging_utils import get_logger
logger = get_logger(__name__)
class LocalSandboxManager:
    """Simplified sandbox manager for local testing"""
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.temp_dir = Path(tempfile.mkdtemp(prefix="ridges_local_test_"))
        self.agents_dir = self.temp_dir / "agents"
        self.repos_dir = self.temp_dir / "repos"
        self.repo_cache_dir = self.temp_dir / "repo_cache"
        # Import docker here to handle ImportError properly
        try:
            import docker
            from docker.models.containers import Container
            self.docker_module = docker
            self.Container = Container
        except ImportError:
            raise ImportError("Docker Python library is required for local testing. Install with: pip install docker")
        # Create directories
        self.agents_dir.mkdir(parents=True)
        self.repos_dir.mkdir(parents=True)
        self.repo_cache_dir.mkdir(parents=True)
        # Initialize Docker client
        try:
            self.docker = self.docker_module.from_env()
            self.docker.ping()
        except Exception as e:
            raise RuntimeError(f"Docker is not running or accessible: {e}")
        # Create network for container communication
        self.network_name = f"local-test-{uuid.uuid4().hex[:8]}"
        try:
            self.network = self.docker.networks.create(
                self.network_name,
                driver="bridge",
                check_duplicate=True
            )
            if self.verbose:
                print(f"Created network: {self.network_name}")
        except self.docker_module.errors.APIError as e:
            if "already exists" in str(e):
                self.network = self.docker.networks.get(self.network_name)
            else:
                raise
        # Start proxy container
        self.proxy_container_name = "sandbox-proxy"
        try:
            # Check if proxy container already exists
            existing = self.docker.containers.get(self.proxy_container_name)
            existing.remove(force=True)
        except self.docker_module.errors.NotFound:
            pass
        # Pull proxy image if needed
        try:
            self.docker.images.get(PROXY_DOCKER_IMAGE)
        except self.docker_module.errors.ImageNotFound:
            print(f"Pulling proxy image: {PROXY_DOCKER_IMAGE}")
            self.docker.images.pull(PROXY_DOCKER_IMAGE)
        # Start proxy container on the custom network, but configure it to reach the host's proxy service
        host_proxy_url = os.getenv('RIDGES_PROXY_URL', 'http://localhost:8001')
        # Parse the URL and replace the hostname with host.docker.internal for Docker containers
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(host_proxy_url)
        # Replace any hostname/IP with host.docker.internal so containers can reach the host
        host_proxy_url = urlunparse(parsed._replace(netloc=f'host.docker.internal:{parsed.port or 8001}'))
        
        self.proxy_container = self.docker.containers.run(
            PROXY_DOCKER_IMAGE,
            name=self.proxy_container_name,
            network=self.network_name,
            detach=True,
            remove=False,
            extra_hosts={
                'host.docker.internal': 'host-gateway'
            },
            environment={
                'RIDGES_API_URL': os.getenv('RIDGES_API_URL', 'http://localhost:8000'),
                'RIDGES_PROXY_URL': host_proxy_url,
            }
        )
        if self.verbose:
            print(f"Started proxy container: {self.proxy_container_name}")
        # Wait for proxy to be ready
        import time
        time.sleep(2)
        # Use proxy container name for internal Docker network communication
        # The proxy listens on port 80 (default for nginx)
        self.proxy_url = f"http://{self.proxy_container_name}"
        if self.verbose:
            print(f"Using proxy URL: {self.proxy_url}")
    async def create_sandbox(self, problem: SwebenchProblem, agent_file: Path, log_buffer: list = None) -> 'LocalSandbox':
        """Create a new sandbox for testing"""
        # Copy agent file to temp directory
        agent_name = f"agent_{uuid.uuid4().hex[:8]}.py"
        agent_path = self.agents_dir / agent_name
        shutil.copy2(agent_file, agent_path)
        # Create evaluation run
        evaluation_run = EvaluationRun(
            run_id=str(uuid.uuid4()),
            evaluation_id=str(uuid.uuid4()),
            validator_hotkey="local-test",
            swebench_instance_id=problem.instance_id,
            status="sandbox_created",
            started_at=datetime.now(timezone.utc),
            sandbox_created_at=datetime.now(timezone.utc)
        )
        # Create sandbox
        sandbox = LocalSandbox(
            evaluation_run=evaluation_run,
            problem=problem,
            agent_path=agent_path,
            local_manager=self,
            verbose=self.verbose,
            log_buffer=log_buffer
        )
        return sandbox
    def cleanup(self):
        """Clean up all resources"""
        try:
            # Stop and remove proxy container
            if hasattr(self, 'proxy_container'):
                self.proxy_container.stop()
                self.proxy_container.remove()
                if self.verbose:
                    print(f"Removed proxy container: {self.proxy_container_name}")
        except Exception as e:
            if self.verbose:
                print(f"Error removing proxy container: {e}")
        try:
            # Remove network
            if hasattr(self, 'network'):
                self.network.remove()
                if self.verbose:
                    print(f"Removed network: {self.network_name}")
        except Exception as e:
            if self.verbose:
                print(f"Error removing network: {e}")
        try:
            # Remove temp directory
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir, ignore_errors=True)
                if self.verbose:
                    print(f"Removed temp directory: {self.temp_dir}")
        except Exception as e:
            if self.verbose:
                print(f"Error removing temp directory: {e}")
class LocalSandbox:
    """Local sandbox for testing agents without database dependencies"""
    def __init__(self, evaluation_run: EvaluationRun, problem: SwebenchProblem, agent_path: Path, local_manager: LocalSandboxManager, verbose: bool = False, log_buffer: list = None):
        self.evaluation_run = evaluation_run
        self.problem = problem
        self.agent_path = agent_path
        self.local_manager = local_manager
        self.verbose = verbose
        self.container = None
        self.repo_dir = None
        self._cancelled = asyncio.Event()
        self.log_buffer = log_buffer  # Set immediately from constructor
        self.container_logs = None  # Will store logs before container removal
        # Use the docker module from the manager
        self.docker_module = local_manager.docker_module
        
        # Set up repo directory
        self.repo_dir = self.local_manager.repos_dir / f"repo_{self.evaluation_run.run_id}"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        # Clone repository
        clone_repo(
            self.repo_dir,
            problem.repo,
            problem.base_commit
        )
        self._log(f"📁 Repository cloned to: {self.repo_dir}")
    
    def _log(self, message: str):
        """Log a message to buffer if available, otherwise print"""
        if self.log_buffer is not None:
            self.log_buffer.append(message)
        elif self.verbose:
            print(message)
    async def run(self) -> None:
        """Run the sandbox - compatibility method for runner"""
        result = await self.run_agent()
        # Store result in evaluation_run for runner compatibility
        if result['status'] == 'COMPLETED':
            self.evaluation_run.solved = result['solved']
        elif result['status'] == 'FAILED':
            self.evaluation_run.solved = False
            self.evaluation_run.error = result['error']
        elif result['status'] == 'ERROR':
            self.evaluation_run.solved = False
            self.evaluation_run.error = result['error']
    async def run_agent(self) -> Dict[str, Any]:
        """Run the agent in the sandbox"""
        try:
            # Prepare sandbox input
            sandbox_input = SandboxInput(
                instance_id=self.problem.instance_id,
                problem_statement=self.problem.problem_statement,
                repo=self.problem.repo,
                base_commit=self.problem.base_commit,
                run_id=self.evaluation_run.run_id,
            )
            # Write input file  
            input_file = self.repo_dir / "input.json"
            input_file.write_text(sandbox_input.model_dump_json())
            # Copy agent file to sandbox
            src_dir = self.repo_dir / "src"
            src_dir.mkdir(exist_ok=True)
            agent_dest = src_dir / "agent.py"
            shutil.copy2(self.agent_path, agent_dest)
            
            # Copy the agent_runner.py from the sandbox module
            runner_src = Path(__file__).parent.parent / "sandbox" / "agent_runner.py"
            runner_dest = self.repo_dir / "agent_runner.py"
            shutil.copy2(runner_src, runner_dest)
            # Pull sandbox image if needed
            try:
                self.local_manager.docker.images.get(SANDBOX_DOCKER_IMAGE)
            except self.docker_module.errors.ImageNotFound:
                self._log(f"📥 Pulling sandbox image: {SANDBOX_DOCKER_IMAGE}")
                self.local_manager.docker.images.pull(SANDBOX_DOCKER_IMAGE)
            # Run sandbox container
            self.container = self.local_manager.docker.containers.run(
                SANDBOX_DOCKER_IMAGE,
                f"python agent_runner.py",
                volumes={
                    str(self.repo_dir): {"bind": SANDBOX_DIR, "mode": "rw"}
                },
                working_dir=SANDBOX_DIR,
                network=self.local_manager.network_name,
                detach=True,
                remove=False,
                mem_limit=f"{SANDBOX_MAX_RAM_USAGE}m",
                environment={
                    'AI_PROXY_URL': self.local_manager.proxy_url,
                    'RIDGES_PROXY_URL': self.local_manager.proxy_url,
                    'RIDGES_API_URL': os.getenv('RIDGES_API_URL', 'http://localhost:8000'),
                }
            )
            self._log("🐳 Started sandbox container")
            # Monitor container
            monitor_task = asyncio.create_task(self._monitor_container())
            # Wait for container to complete
            await monitor_task
            # Read output
            output_file = self.repo_dir / "output.json"
            if output_file.exists():
                with open(output_file, 'r') as f:
                    output_data = json.load(f)
                # Check if agent_runner.py wrapped the output
                if output_data.get('success') and 'output' in output_data:
                    patch = output_data['output'].get('patch', '')
                else:
                    patch = output_data.get('patch', '')
                if patch:
                    self.evaluation_run.response = patch
                    self.evaluation_run.status = "patch_generated"
                    # Run SWE-bench evaluation
                    await self._run_swe_bench_evaluation()
                    return {
                        'instance_id': self.problem.instance_id,
                        'status': 'COMPLETED',
                        'solved': self.evaluation_run.solved,
                        'patch_generated': True,
                        'patch_length': len(patch),
                        'error': self.evaluation_run.error
                    }
                else:
                    # Use pre-captured container logs
                    container_logs = f"\n{self.container_logs}" if self.container_logs else ""
                    
                    # Create detailed error message
                    error_parts = [
                        "Empty patch returned from agent.",
                        f"Raw output data: {json.dumps(output_data, indent=2)[:1000]}..."
                    ]
                    if container_logs:
                        error_parts.append(container_logs)
                    
                    detailed_error = "\n".join(error_parts)
                    
                    self.evaluation_run.status = "no_patch_generated"
                    self._log(f"❌ Sandbox {self.evaluation_run.run_id} failed: {detailed_error}")
                    
                    return {
                        'instance_id': self.problem.instance_id,
                        'status': 'FAILED',
                        'solved': False,
                        'error': detailed_error,
                        'patch_generated': False,
                        'patch_length': 0
                    }
            else:
                # Use pre-captured container logs
                container_logs = f"\n{self.container_logs}" if self.container_logs else ""
                
                # Create detailed error message
                error_parts = [
                    "No output file generated by agent.",
                    f"Expected output file: {output_file}"
                ]
                if container_logs:
                    error_parts.append(container_logs)
                
                detailed_error = "\n".join(error_parts)
                
                self.evaluation_run.status = "no_output_file"
                self._log(f"❌ Sandbox {self.evaluation_run.run_id} failed: {detailed_error}")
                
                return {
                    'instance_id': self.problem.instance_id,
                    'status': 'FAILED',
                    'solved': False,
                    'error': detailed_error,
                    'patch_generated': False,
                    'patch_length': 0
                }
        except Exception as e:
            # Use pre-captured container logs
            container_logs = f"\n{self.container_logs}" if self.container_logs else ""
            
            # Create detailed error message
            import traceback
            error_parts = [
                f"Sandbox execution failed: {str(e)}",
                f"Traceback: {traceback.format_exc()}"
            ]
            if container_logs:
                error_parts.append(container_logs)
            
            detailed_error = "\n".join(error_parts)
            
            self._log(f"❌ Sandbox execution failed: {detailed_error}")
            self.evaluation_run.status = "execution_failed"
            self.evaluation_run.error = detailed_error
            
            return {
                'instance_id': self.problem.instance_id,
                'status': 'ERROR',
                'solved': False,
                'error': detailed_error,
                'patch_generated': False,
                'patch_length': 0
            }
    async def _run_swe_bench_evaluation(self) -> None:
        """Run SWE-bench evaluation on the generated patch"""
        try:
            from swebench.harness.run_evaluation import load_swebench_dataset, run_instance, make_test_spec, build_env_images
            
            # Load instance and create prediction
            instance_id = self.problem.instance_id
            instance = load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test", [instance_id])[0]
            prediction = {
                "instance_id": instance_id,
                "model_name_or_path": self.evaluation_run.run_id,
                "model_patch": self.evaluation_run.response,
            }
            test_spec = make_test_spec(instance)
            
            # Build environment and run evaluation
            build_env_images(self.local_manager.docker, [test_spec], max_workers=4)
            result = run_instance(
                test_spec=test_spec,
                pred=prediction,
                rm_image=False,
                force_rebuild=False,
                client=self.local_manager.docker,
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
            self._log(f"❌ SWE-bench evaluation failed: {e}")
            self.evaluation_run.error = f"SWE-bench evaluation failed: {str(e)}"
            self.evaluation_run.solved = False
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
                if self.evaluation_run.sandbox_created_at:
                    runtime = (datetime.now(timezone.utc) - self.evaluation_run.sandbox_created_at).total_seconds()
                    if runtime > SANDBOX_MAX_RUNTIME:
                        self.container.kill()
                        raise TimeoutError(f"Runtime limit exceeded: {runtime:.1f}s")
            except Exception as e:
                error_str = str(e).lower()
                if "exited" in error_str or "not found" in error_str or "no such container" in error_str:
                    # Container has exited or been removed
                    break
                logger.warning(f"Container monitoring error: {e}")
            await asyncio.sleep(1)
        
        # Capture container logs before removal
        try:
            if self.container:
                logs = self.container.logs(stdout=True, stderr=True, tail=100).decode('utf-8', errors='ignore')
                self.container_logs = f"Container logs (last 100 lines):\n{logs}"
        except Exception as e:
            self.container_logs = f"Failed to capture container logs: {e}"
        
        # Clean up container
        try:
            self.container.remove()
        except Exception:
            pass
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
    async def cancel(self) -> None:
        """Cancel sandbox execution"""
        self._cancelled.set() 