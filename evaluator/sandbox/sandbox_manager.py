"""Sandbox Manager for Agent Isolation and Execution

This module provides the core infrastructure for running agent code in isolated Docker containers.
The sandbox architecture ensures security through complete network isolation, with controlled
access to the inference gateway via a proxy server.

Architecture Overview:
--------------------
1. Network Isolation: Sandboxes run on an internal Docker network with no direct internet access
2. Proxy Server: A dedicated proxy container (nginx) bridges sandboxed agents to the inference gateway
3. Volume Mounting: Agent code and data are mounted via temporary directories
4. Resource Cleanup: All containers and temp directories are automatically cleaned up after execution

Security Model:
---------------
- Sandboxes cannot directly access the internet (internal-only Docker network)
- All inference requests must go through the proxy server
- Each sandbox gets its own isolated filesystem via volume mounts
- Containers are destroyed immediately after execution (no lingering state)
- Resource limits and timeouts prevent runaway processes

Usage Flow:
-----------
1. SandboxManager initializes the network and proxy on startup
2. initialize_sandbox() creates a new container with agent code
3. run_sandbox() executes the agent and collects results
4. Cleanup happens automatically in run_sandbox() via try/finally
"""

import os
import json
import httpx
import shutil
import utils.logger as logger

from typing import Any, Dict, Callable
from utils.temp import create_temp_dir, delete_temp_dir
from evaluator.models import Sandbox, SandboxResultWithLogs
from utils.docker import DOCKER_PREFIX, get_docker_client, build_docker_image, create_internal_docker_network, connect_docker_container_to_internet, stop_and_delete_all_docker_containers



# Docker network configuration for sandbox isolation
# This internal network prevents sandboxes from accessing the internet directly
SANDBOX_NETWORK_NAME = f"{DOCKER_PREFIX}-sandbox-network"

# The proxy container acts as the only bridge between sandboxes and external services
# It forwards requests to the inference gateway while maintaining network isolation
SANDBOX_PROXY_HOST = f"{DOCKER_PREFIX}-sandbox-proxy"
SANDBOX_PROXY_PORT = 80



class SandboxManager:
    """Manages the lifecycle of isolated sandbox environments for agent execution.
    
    This class handles:
    - Docker network setup (internal, isolated network)
    - Proxy container management (bridges sandboxes to inference gateway)
    - Sandbox container creation and execution
    - Resource cleanup and lifecycle management
    
    Attributes:
        sandboxes: Dictionary tracking active sandbox instances
        proxy_container: The nginx proxy Docker container
        proxy_temp_dir: Temporary directory for proxy-related files
    
    Example:
        manager = SandboxManager("http://inference-gateway:8000")
        sandbox = manager.initialize_sandbox(
            name="agent-123",
            script_path="/path/to/agent.py",
            input_data={"problem": "fix this bug"},
            timeout_seconds=300
        )
        result = manager.run_sandbox(sandbox)
    """
    
    def __init__(self, inference_gateway_url: str):
        """Initialize the SandboxManager with required infrastructure.
        
        This method:
        1. Validates connectivity to the inference gateway
        2. Cleans up any existing Docker containers
        3. Creates the isolated internal Docker network
        4. Builds the sandbox and proxy Docker images
        5. Starts the proxy container
        
        Args:
            inference_gateway_url: URL of the inference gateway service
            
        Raises:
            System exit if inference gateway is unreachable or Docker setup fails
        """
        # Validate that we can reach the inference gateway before setting up infrastructure
        self._check_inference_gateway(inference_gateway_url)

        # Clean up any existing containers from previous runs to ensure fresh state
        stop_and_delete_all_docker_containers()

        # Create the internal Docker network that isolates sandboxes from the internet
        create_internal_docker_network(SANDBOX_NETWORK_NAME)

        # Build the main sandbox image unless explicitly disabled (useful for development)
        if os.getenv("CXII_NO_BUILD_SANDBOX_IMAGE") is None:
            build_docker_image(os.path.dirname(__file__), "sandbox-image")
        
        # Dictionary to track active sandboxes (primarily for debugging/monitoring)
        self.sandboxes = {}

        # Initialize proxy components
        self.proxy_container = None
        self.proxy_temp_dir = None
        
        # Build and start the proxy container that bridges sandboxes to the inference gateway
        build_docker_image(os.path.dirname(__file__) + "/proxy", "sandbox-proxy-image")
        self._create_sandbox_proxy(inference_gateway_url)



    def _check_inference_gateway(self, inference_gateway_url):
        """Validate connectivity to the inference gateway.
        
        Performs a basic HTTP GET to verify the inference gateway is reachable.
        This is critical because sandboxes rely on the proxy -> gateway connection
        for all LLM and embedding requests.
        
        Args:
            inference_gateway_url: URL to check connectivity against
            
        Note:
            TODO: Currently only checks basic connectivity. Should validate
            actual inference and embedding request capability.
        """
        logger.info(f"Checking inference gateway URL: {inference_gateway_url}")

        valid = False
        try:
            httpx.get(inference_gateway_url)

            # TODO ADAM: Send inference & embedding requests

            valid = True
        except Exception as e:
            pass

        if not valid:
            logger.fatal(f"Inference gateway URL {inference_gateway_url} is invalid")
        
        logger.info(f"Inference gateway URL {inference_gateway_url} is valid")



    def _create_sandbox_proxy(self, gateway_url):
        """Create and configure the sandbox proxy container (nginx).
        
        The proxy server is the ONLY container with internet access. It serves as a
        controlled bridge between isolated sandboxes and the inference gateway.
        
        Architecture:
            - Sandboxes (isolated) --> Proxy (bridged) --> Inference Gateway (internet)
            - Proxy runs on the internal network so sandboxes can reach it
            - Proxy is also connected to the default bridge network for internet access
            
        Security:
            - Sandboxes cannot bypass the proxy (no internet access)
            - Proxy only forwards requests to the configured gateway URL
            - Proxy configuration is generated from a template at runtime
            
        Args:
            gateway_url: The full URL of the inference gateway to proxy to
            
        Environment Variables:
            GATEWAY_URL: Full URL of the inference gateway (e.g., http://host:8000)
            GATEWAY_HOST: Hostname extracted from gateway_url for proxy headers
        """
  
        logger.info("Running sandbox proxy")

        self.proxy_container = get_docker_client().containers.run(
            name=SANDBOX_PROXY_HOST,
            image=f"{DOCKER_PREFIX}-sandbox-proxy-image",
            network=SANDBOX_NETWORK_NAME,
            environment={
                "GATEWAY_URL": gateway_url,
                "GATEWAY_HOST": gateway_url.split("://")[1].split(":")[0]
            },
            detach=True
        )

        # Connect proxy to the default bridge network to give it internet access
        # This allows the proxy to forward requests from isolated sandboxes to the gateway
        connect_docker_container_to_internet(self.proxy_container)



    def initialize_sandbox(
        self,
        *,
        name: str,
        script_path: str,
        input_data: Any = None,
        env_vars: Dict[str, str] = {},
        on_mount: Callable[[str], None] = None,
        timeout_seconds: int = None
    ) -> Sandbox:
        """Create and configure a new sandbox container for code execution.
        
        This method creates a fully isolated Docker container with:
        - A temporary directory mounted at /sandbox for code and data
        - The provided script ready to execute
        - Input data serialized to input.json
        - Access to the proxy for inference requests
        - No direct internet access (network isolation)
        
        Args:
            name: Unique identifier for this sandbox (will be prefixed with DOCKER_PREFIX)
            script_path: Path to the script to run inside the sandbox (.py or .js)
            input_data: JSON-serializable data passed to the script via input.json
            env_vars: Additional environment variables to set in the container
            on_mount: Optional callback to customize the temp directory before container starts
                     Receives the temp directory path as its argument
            timeout_seconds: Maximum execution time (enforced by run_sandbox)
            
        Returns:
            Sandbox instance containing container reference and metadata
            
        Raises:
            ValueError: If script_path does not end in .py or .js
            
        File Layout Inside Container:
            /sandbox/
                ├── input.json      # Serialized input_data
                ├── output.json     # Script writes results here
                ├── <script>        # The provided script file
                └── repo/           # Optional: mounted repository (if on_mount creates it)
                    
        Environment Inside Container:
            - PYTHONUNBUFFERED=1: Prevent Python output buffering
            - PYTHONDONTWRITEBYTECODE=1: Disable __pycache__ generation
            - SANDBOX_PROXY_URL: URL for inference gateway access (via proxy)
            - Additional env_vars as provided
        """
        name = f"{DOCKER_PREFIX}-{name}"
        
        # Create temporary directory
        temp_dir = create_temp_dir()
        logger.debug(f"Created temporary directory for sandbox <{name}>: {temp_dir}")
        
        if on_mount is not None:
            # Call on_mount
            logger.debug(f"Calling on_mount() for sandbox <{name}>...")
            on_mount(temp_dir)
            logger.debug(f"Called on_mount() for sandbox <{name}>")

        # Python and JavaScript
        script_name = os.path.basename(script_path)
        script_extension = os.path.splitext(script_name)[1]
        if script_extension not in [".py", ".js"]:
            raise ValueError(f"Invalid script extension: {script_extension}")

        # Copy script
        temp_script_path = os.path.join(temp_dir, script_name)
        shutil.copy2(script_path, temp_script_path)
        logger.debug(f"Copied script for sandbox <{name}>: {script_name} --> {temp_script_path}")
        
        # Create input.json
        temp_input_json_path = os.path.join(temp_dir, "input.json")
        with open(temp_input_json_path, "w") as f:
            json.dump(input_data, f, indent=2)
        logger.debug(f"Created input.json for sandbox <{name}>: {temp_input_json_path}")

        # Create command
        if script_extension == ".py":
            command = f"python /sandbox/{script_name} 2>&1"
        elif script_extension == ".js":
            command = f"node /sandbox/{script_name} 2>&1"

        # Create Docker container
        container = get_docker_client().containers.run(
            name=name,
            image=f"{DOCKER_PREFIX}-sandbox-image",
            volumes={temp_dir: {"bind": "/sandbox", "mode": "rw"}},
            network=SANDBOX_NETWORK_NAME,
            environment={
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1", # No __pycache__
                "SANDBOX_PROXY_URL": f"http://{SANDBOX_PROXY_HOST}:{SANDBOX_PROXY_PORT}",
                **env_vars
            },
            command=command,
            detach=True
        )

        return Sandbox(
            name=name,
            temp_dir=temp_dir,
            container=container,
            timeout_seconds=timeout_seconds
        )



    def run_sandbox(
        self,
        sandbox: Sandbox
    ) -> SandboxResultWithLogs:
        """Execute a sandbox container and collect its results.
        
        This method:
        1. Waits for the container to finish (with timeout)
        2. Reads the output.json produced by the script
        3. Collects all container logs (stdout/stderr)
        4. Cleans up the container and temporary directory
        
        Args:
            sandbox: The Sandbox instance to execute (created by initialize_sandbox)
            
        Returns:
            SandboxResultWithLogs containing success status, output data, error info, and logs
            
        Raises:
            requests.exceptions.ConnectionError: If the container times out
                (Docker SDK throws this instead of TimeoutError due to a known bug)
            
        Cleanup Behavior:
            Container and temp directory are ALWAYS cleaned up, even if execution fails,
            due to the try/finally block. This prevents resource leaks.
            
        Expected Output Format:
            The script should write a JSON object to /sandbox/output.json with:
            {
                "success": true/false,
                "output": <any> (if success),
                "error": <string> (if not success),
                "traceback": <string> (if not success)
            }
        """
        
        try:
            # Wait for the container to finish, with optional timeout
            # If timeout is exceeded, Docker SDK throws ConnectionError (not TimeoutError)
            # See: https://github.com/docker/docker-py/issues/2268
            sandbox.container.wait(timeout=sandbox.timeout_seconds)

            # The script should have written results to output.json
            temp_output_json_path = os.path.join(sandbox.temp_dir, "output.json")
            with open(temp_output_json_path, "r") as f:
                output = json.load(f)
            logger.debug(f"Loaded output.json for sandbox <{sandbox.name}>: {temp_output_json_path}")

            # Capture all stdout/stderr from the container for debugging
            logs = sandbox.container.logs().decode("utf-8")
            # logger.info(logs)

            return SandboxResultWithLogs(**output, logs=logs)
        finally:
            # CRITICAL: Always clean up resources, even on failure
            # Stop and remove the container
            sandbox.container.stop()
            sandbox.container.remove()

            # Delete the temporary directory with all mounted files
            delete_temp_dir(sandbox.temp_dir)