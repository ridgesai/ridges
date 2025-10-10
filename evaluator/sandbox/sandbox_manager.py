"""SandboxManager class for creating and managing Docker sandboxes."""

import os
import json
import time
import httpx
import shutil
import docker
import threading
import utils.logger as logger

from pydantic import BaseModel, ConfigDict
from typing import Any, Dict, Optional, Callable
from utils.temp import create_temp_dir, cleanup_temp_dir
from utils.docker import docker_client, build_docker_image, create_internal_docker_network, connect_docker_container_to_internet, stop_and_delete_all_docker_containers



SANDBOX_NETWORK_NAME = "sandbox-network"

SANDBOX_PROXY_HOST = "sandbox_proxy"
SANDBOX_PROXY_PORT = 80



class Sandbox(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True) # Because of docker.models.containers.Container

    name: str
    temp_dir: str
    container: docker.models.containers.Container



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
        build_docker_image(os.path.dirname(__file__), "sandbox-image")
        self.sandboxes = {}

        # Setup sandbox-proxy
        self.proxy_container = None
        self.proxy_temp_dir = None
        build_docker_image(os.path.dirname(__file__) + "/proxy", "sandbox-proxy-image")
        self._create_sandbox_proxy(inference_gateway_url)
        
        # Setup watchdog
        self._start_watchdog()



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



    def _start_watchdog(self):
        self.watchdog_thread = threading.Thread(target=self._watchdog)
        self.watchdog_thread.daemon = True
        logger.info("Starting sandbox watchdog thread")
        self.watchdog_thread.start()

    def _watchdog(self):
        logger.info("Started sandbox watchdog thread")

        while True:
            time.sleep(1)

            # TODO



    def initialize_sandbox(
        self,
        *,
        name: str,
        on_mount: Callable[[str], None],
        env_vars: Dict[str, str],
        python_script_path: str,
        input_data: Any
    ) -> Sandbox:
        # Create temporary directory
        temp_dir = create_temp_dir()
        logger.debug(f"Created temporary directory for sandbox <{name}>: {temp_dir}")
        
        # Call on_mount
        logger.debug(f"Calling on_mount() for sandbox <{name}>...")
        on_mount(temp_dir)
        logger.debug(f"Called on_mount() for sandbox <{name}>")

        # Copy Python script
        python_script_name = os.path.basename(python_script_path)
        temp_python_script_path = os.path.join(temp_dir, python_script_name)
        shutil.copy2(python_script_path, temp_python_script_path)
        logger.debug(f"Copied Python script for sandbox <{name}>: {python_script_path} --> {temp_python_script_path}")
        
        # Create input.json
        temp_input_json_path = os.path.join(temp_dir, "input.json")
        with open(temp_input_json_path, "w") as f:
            json.dump(input_data, f, indent=2)
        logger.debug(f"Created input.json for sandbox <{name}>: {temp_input_json_path}")

        # Create Docker container
        container = docker_client.containers.run(
            name=name,
            image="sandbox-image",
            command=f"python /sandbox/{python_script_name} 2>&1",
            volumes={
                temp_dir: {"bind": "/sandbox", "mode": "rw"}
            },
            environment={
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1", # No __pycache__
                "SANDBOX_PROXY_URL": f"http://{SANDBOX_PROXY_HOST}:{SANDBOX_PROXY_PORT}",
                **env_vars
            },
            network=SANDBOX_NETWORK_NAME,
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
    ) -> Any:
        
        container: docker.models.containers.Container = sandbox.container

        container.wait(timeout=timeout_seconds)


        pass









    def _run_sandbox(self, sandbox_id):
        """Run Docker container for the given sandbox (this runs on a background thread)."""
       

        # Try and read output.json
        output_json_path = os.path.join(temp_dir, "output.json")
        try:
            with open(output_json_path, "r") as f:
                output = json.load(f)
            debug(f"[SANDBOX] Read output.json from <{sandbox_id}>: {output_json_path}")
        except Exception as e:
            finish_with_error(f"Failed to read output.json: {e}", result)
            return



        # Validate output.json structure
        if "status" not in output:
            finish_with_error("output.json does not contain a status field", result)
            return

        if output["status"] == "success":
            if "output" not in output:
                finish_with_error("output.json has status 'success' but does not contain an output field", result)
                return
            result["output"] = output["output"]
        elif output["status"] == "error":
            if "error" not in output:
                finish_with_error("output.json has status 'error' but does not contain an error field", result)
                return
            
            result["traceback"] = output.get("traceback")
            finish_with_error(output["error"], result)
            return
        else:
            finish_with_error(f"output.json contains an invalid status field: {output['status']}", result)
            return
        
        try:
            sandbox["on_finish"](result)
        except Exception as e:
            warn(f"[SANDBOX] on_finish() callback failed for <{sandbox_id}>: {e}")
        finally:
            self.cleanup_sandbox(sandbox_id)










    def _create_sandbox_proxy(self, gateway_url):
        """
        Create the sandbox proxy server.
        
        This is a special sandbox that runs a proxy server (nginx).
        This is the only sandbox that can access the internet.
        
        The other sandboxes cannot directly access the internet.
        So to do inference, they send requests to this proxy server, which forwards appropriate requests to the inferencegateway.
        """
  
        logger.info("Running sandbox proxy")

        self.proxy_container = docker_client.containers.run(
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