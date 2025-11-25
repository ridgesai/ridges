"""SandboxManager class for creating and managing Docker sandboxes."""

import os
import json
import httpx
import shutil
import utils.logger as logger

from typing import Any, Dict, Optional, Callable
from utils.temp import create_temp_dir, delete_temp_dir
from evaluator.models import Sandbox, SandboxResultWithLogs
from utils.docker import get_docker_client, build_docker_image, create_internal_docker_network, connect_docker_container_to_internet, stop_and_delete_all_docker_containers



SANDBOX_NETWORK_NAME = "sandbox-network"

SANDBOX_PROXY_HOST = "sandbox_proxy"
SANDBOX_PROXY_PORT = 80



class SandboxManager:
    """Manages Docker sandbox creation and execution."""



    def __init__(self, inference_gateway_url: str):
        # Setup inference gateway
        self._check_inference_gateway(inference_gateway_url)

        # Setup Docker
        stop_and_delete_all_docker_containers()

        # Setup sandbox-network
        create_internal_docker_network(SANDBOX_NETWORK_NAME)

        # Setup sandbox-image
        if os.getenv("CXII_NO_BUILD_SANDBOX_IMAGE") is None:
            build_docker_image(os.path.dirname(__file__), "sandbox-image")
        self.sandboxes = {}

        # Setup sandbox-proxy
        self.proxy_container = None
        self.proxy_temp_dir = None
        build_docker_image(os.path.dirname(__file__) + "/proxy", "sandbox-proxy-image")
        self._create_sandbox_proxy(inference_gateway_url)



    def _check_inference_gateway(self, inference_gateway_url):
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
        """
        Create the sandbox proxy server.
        
        This is a special sandbox that runs a proxy server (nginx).
        This is the only sandbox that can access the internet.
        
        The other sandboxes cannot directly access the internet.
        So to do inference, they send requests to this proxy server, which forwards appropriate requests to the inference gateway.
        """
  
        logger.info("Running sandbox proxy")

        self.proxy_container = get_docker_client().containers.run(
            name=SANDBOX_PROXY_HOST,
            image="sandbox-proxy-image",
            network=SANDBOX_NETWORK_NAME,
            environment={
                "GATEWAY_URL": gateway_url,
                "GATEWAY_HOST": gateway_url.split("://")[1].split(":")[0]
            },
            detach=True
        )

        connect_docker_container_to_internet(self.proxy_container)



    def initialize_sandbox(
        self,
        *,
        name: str,
        script_path: str,
        input_data: Any,
        env_vars: Dict[str, str] = {},
        on_mount: Callable[[str], None] = None
    ) -> Sandbox:
        # Create temporary directory
        temp_dir = create_temp_dir()
        logger.debug(f"Created temporary directory for sandbox <{name}>: {temp_dir}")
        
        if on_mount is not None:
            # Call on_mount
            logger.debug(f"Calling on_mount() for sandbox <{name}>...")
            on_mount(temp_dir)
            logger.debug(f"Called on_mount() for sandbox <{name}>")

        # Python and JavaScript
        script_extension = os.path.splitext(script_name)[1]
        if script_extension not in [".py", ".js"]:
            raise ValueError(f"Invalid script extension: {script_extension}")

        # Copy script
        script_name = os.path.basename(script_path)
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
            image="sandbox-image",
            volumes={temp_dir: {"bind": "/sandbox", "mode": "rw"}},
            network=SANDBOX_NETWORK_NAME,
            user=f"{os.getuid()}:{os.getgid()}",
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
            container=container
        )



    def run_sandbox(
        self,
        sandbox: Sandbox,
        *,
        timeout_seconds: Optional[int] = None
    ) -> SandboxResultWithLogs:
        
        try:
            sandbox.container.wait(timeout=timeout_seconds)

            # Load /sandbox/output.json
            temp_output_json_path = os.path.join(sandbox.temp_dir, "output.json")
            with open(temp_output_json_path, "r") as f:
                output = json.load(f)
            logger.debug(f"Loaded output.json for sandbox <{sandbox.name}>: {temp_output_json_path}")

            # Get logs
            logs = sandbox.container.logs().decode("utf-8")

            return SandboxResultWithLogs(**output, logs=logs)
        finally:
            # Remove Docker container
            sandbox.container.stop()
            sandbox.container.remove()

            # Remove temporary directory
            delete_temp_dir(sandbox.temp_dir)