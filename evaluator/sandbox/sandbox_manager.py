"""SandboxManager for creating and managing Docker sandboxes."""

import os
import json
import time
import docker
import shutil
import requests
import threading
import traceback
import subprocess

from enum import Enum

from validator.utils.docker import build_docker_image
from validator.utils.logger import debug, info, warn, error
from validator.utils.temp import create_temp_dir, cleanup_temp_dir



SANDBOX_NETWORK_NAME = "sandbox-network"

class SandboxNetworkMode(Enum):
    # Restricted to sandbox-only local network (default)
    SANDBOX = "sandbox"
    
    # Full Internet access
    PUBLIC = "public"
    
    # Connected to both Docker internal network and Internet
    BOTH = "both"



SANDBOX_PROXY_HOST = "sandbox_proxy"
SANDBOX_PROXY_PORT = 80



class SandboxManager:
    """Manages Docker sandbox creation and execution."""



    def __init__(self, gateway_url, *, log_docker_to_stdout=False):
        self._check_gateway(gateway_url)



        try:
            self.docker = docker.from_env()
        except Exception as e:
            error(f"[SANDBOX] Failed to create Docker client: {e}")

        debug(f"[SANDBOX] Stopping and deleting all containers")
        for container in self.docker.containers.list(all=True):
            try:
                container.stop(timeout=3)
            except Exception as e:
                warn(f"[SANDBOX] Could not stop container {container.name}: {e}")
            try:
                container.remove(force=True)
            except Exception as e:
                warn(f"[SANDBOX] Could not remove container {container.name}: {e}")
        self.docker.containers.prune()
        debug(f"[SANDBOX] Stopped and deleted all containers")



        build_docker_image(os.path.dirname(__file__), "sandbox-image")
        self.sandboxes = {}
        
        self._create_sandbox_network()

        self.proxy_container = None
        self.proxy_temp_dir = None
        build_docker_image(os.path.dirname(__file__) + "/proxy", "sandbox-proxy-image")
        self._create_sandbox_proxy(gateway_url)

        self.log_docker_to_stdout = log_docker_to_stdout

        self._start_watchdog()

    def _check_gateway(self, gateway_url):
        info(f"[SANDBOX] Checking gateway URL: {gateway_url}")
        try:
            requests.get(gateway_url)
        except Exception as e:
            error(f"[SANDBOX] Gateway URL {gateway_url} is invalid: {e}")
        info(f"[SANDBOX] Gateway URL {gateway_url} is valid")

    def __del__(self):
        try:
            self.cleanup_all()
            
            if self.proxy_container:
                try:
                    self.proxy_container.stop()
                    self.proxy_container.remove()
                    info("[SANDBOX] Stopped and removed proxy container")
                except Exception as e:
                    warn(f"[SANDBOX] Could not clean up proxy container: {e}")
            
            if self.proxy_temp_dir:
                cleanup_temp_dir(self.proxy_temp_dir)
                debug("[SANDBOX] Cleaned up proxy temp directory")
                
        except Exception:
            pass



    def _start_watchdog(self):
        self.watchdog_thread = threading.Thread(target=self._watchdog)
        self.watchdog_thread.daemon = True
        debug("[SANDBOX] Starting watchdog thread")
        self.watchdog_thread.start()

    def _watchdog(self):
        debug("[SANDBOX] Started watchdog thread")
        while True:
            time.sleep(1)
            for sandbox_id in list(self.sandboxes.keys()):
                sandbox = self.sandboxes[sandbox_id]
                if sandbox["container"]:
                    elapsed = time.time() - sandbox["start_time"]
                    if elapsed > sandbox["timeout"]:
                        warn(f"[SANDBOX] Killing sandbox {sandbox_id} - exceeded timeout of {sandbox['timeout']} seconds (ran for {elapsed:.1f} seconds)")
                        try:
                            sandbox["killed_by_watchdog"] = True
                            sandbox["container"].kill()
                        except Exception as e:
                            warn(f"[SANDBOX] Failed to kill container {sandbox_id}: {e}")



    def create_sandbox(self, *, script_path, input_data, env_vars, on_mount, on_finish, network_mode=SandboxNetworkMode.SANDBOX, timeout=None):
        """
        Create a Docker sandbox that runs the given Python script and provides it with the given input data.
        
        Args:
            script_path: Path to Python script to run in the sandbox, which should write its output to /sandbox/output.json
                On success, it should write:
                    /sandbox/output.json: {
                        "status": "success",
                        "output": *
                    }
                On error, it should write:
                    /sandbox/output.json: {
                        "status": "error",
                        "error": "...",
                        "traceback": "..."
                    }

            input_data: Dictionary to write as input.json in the sandbox

            env_vars: Dictionary of environment variables to set in the sandbox
                SANDBOX_PROXY_URL is set appropriately by default

            on_mount(temp_dir): Callback called after temp dir creation, you can add files to the /sandbox directory here

            on_finish(result): Callback that receives a result when the sandbox finishes
                On success, it receives:
                    result: {
                        "status": "success",
                        "output": </sandbox/output.json> | None,
                        "logs": <stdout+stderr> | None
                    }
                On error, it receives:
                    result: {
                        "status": "error",
                        "error": "..." | None,
                        "traceback": "..." | None,
                        "logs": <stdout+stderr> | None
                    }
            
            network_mode: Network configuration (see SandboxNetworkMode)
                Default: SandboxNetworkMode.SANDBOX

            timeout: Timeout in seconds (or None for no timeout)
                Default: None
             
        Returns:
            sandbox_id: Unique identifier for the created sandbox
        """

        # Create temporary directory on host
        temp_dir = create_temp_dir()
        sandbox_id = f"sandbox_{os.path.basename(temp_dir)}"
        debug(f"[SANDBOX] Created sandbox temp directory for <{sandbox_id}>: {temp_dir}")
        
        # Call on_mount callback
        try:
            on_mount(temp_dir)
        except Exception as e:
            warn(f"[SANDBOX] on_mount({temp_dir}) callback failed for <{sandbox_id}>: {e}")
            try:
                on_finish({"status": "error", "error": f"on_mount() callback failed: {e}", "traceback": traceback.format_exc()})
            except Exception as e:
                warn(f"[SANDBOX] on_finish() callback failed for <{sandbox_id}>: {e}")
            finally:
                self.cleanup_sandbox(sandbox_id)
            return

        # Copy Python script to temp directory
        script_name = os.path.basename(script_path)
        temp_script_path = os.path.join(temp_dir, script_name)
        shutil.copy2(script_path, temp_script_path)
        debug(f"[SANDBOX] Copied main Python script ({script_path}) for <{sandbox_id}>: {temp_script_path}")
        
        # Write input.json to temp directory
        input_json_path = os.path.join(temp_dir, "input.json")
        with open(input_json_path, "w") as f:
            json.dump(input_data, f, indent=2)
        debug(f"[SANDBOX] Written input.json for <{sandbox_id}>: {input_json_path}")

        # Register the sandbox
        self.sandboxes[sandbox_id] = {
            "temp_dir": temp_dir,
            "script_name": script_name,
            "env_vars": env_vars,
            "on_finish": on_finish,
            "network_mode": network_mode,
            "timeout": timeout,
            "start_time": time.time(),
            "container": None,
            "killed_by_watchdog": False
        }

        # Start Docker container in background thread
        thread = threading.Thread(target=self._run_sandbox, args=(sandbox_id,))
        thread.daemon = True
        thread.start()
        
        debug(f"[SANDBOX] Started sandbox runner thread for <{sandbox_id}>")
        return sandbox_id



    def _run_sandbox(self, sandbox_id):
        """Run Docker container for the given sandbox (this runs on a background thread)."""
        
        

        debug(f"[SANDBOX] Running sandbox <{sandbox_id}>")



        # Fetch the sandbox
        sandbox = self.sandboxes.get(sandbox_id)
        if not sandbox:
            warn(f"[SANDBOX] <{sandbox_id}> not found when trying to run it")
            return

        def finish_with_error(error_msg, result):
            warn(f"[SANDBOX] <{sandbox_id}> failed: {error_msg}")
            result["status"] = "error"
            result["error"] = error_msg
            try:
                sandbox["on_finish"](result)
            except Exception as e:
                warn(f"[SANDBOX] on_finish() callback failed for <{sandbox_id}>: {e}")
            finally:
                self.cleanup_sandbox(sandbox_id)
        
        temp_dir = sandbox["temp_dir"]
        script_name = sandbox["script_name"]
        network_mode = sandbox["network_mode"]
        
        result = {"status": "success", "output": None, "logs": "", "error": None, "traceback": None}

        # Try to run the sandbox
        try:
            # Prepare container arguments
            container_args = {
                "image": "sandbox-image",
                "command": f"python /sandbox/{script_name} 2>&1",
                "name": sandbox_id,
                "volumes": {
                    temp_dir: {"bind": "/sandbox", "mode": "rw"}
                },
                "environment": {
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONDONTWRITEBYTECODE": "1", # too many bugs with perms
                    "SANDBOX_PROXY_URL": f"http://{SANDBOX_PROXY_HOST}:{SANDBOX_PROXY_PORT}",
                    **sandbox["env_vars"]
                },
                "remove": False,
                "detach": True
            }
            
            container_args.update(self._get_network_config(network_mode))
            
            # Run the sandbox using Docker. This will block until the sandbox finishes. 
            sandbox["container"] = self.docker.containers.run(**container_args)
            
            # For BOTH mode, connect to the default bridge network as well
            if network_mode == SandboxNetworkMode.BOTH:
                try:
                    bridge_network = self.docker.networks.get("bridge")
                    bridge_network.connect(sandbox["container"])
                    debug(f"[SANDBOX] Connected <{sandbox_id}> to bridge network")
                except Exception as e:
                    warn(f"[SANDBOX] Failed to connect <{sandbox_id}> to bridge network: {e}")

            if self.log_docker_to_stdout:
                for log_line in sandbox["container"].logs(stream=True, follow=True):
                    debug(f"[DOCKER:{sandbox_id}] {log_line.decode('utf-8').rstrip()}")
            else:
                sandbox["container"].wait()

            # Check if container was killed by watchdog before trying to read output
            if sandbox["killed_by_watchdog"]:
                finish_with_error("Container exceeded timeout and was killed", result)
                return
            
            debug(f"[SANDBOX] <{sandbox_id}> finished running")

            logs = sandbox["container"].logs(stderr=False).decode("utf-8")
            result["logs"] = logs
            log_lines = len(logs.splitlines()) if logs else 0
            debug(f"[SANDBOX] <{sandbox_id}> captured {log_lines} lines of logs")
            
            sandbox["container"].remove()
            sandbox["container"] = None

        except Exception as e:
            # An error occurred while running the sandbox
            result["traceback"] = traceback.format_exc()
            finish_with_error(f"failed to run: {e}", result)
            return



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



    def get_sandbox_temp_dir(self, sandbox_id):
        """Get the temporary directory path for a sandbox."""
        
        sandbox_info = self.sandboxes.get(sandbox_id)
        if not sandbox_info:
            return None
        return sandbox_info["temp_dir"]



    def cleanup_sandbox(self, sandbox_id):
        """Clean up a sandbox and its temporary directory."""

        sandbox_info = self.sandboxes.get(sandbox_id)
        if not sandbox_info:
            warn(f"[SANDBOX] Sandbox <{sandbox_id}> not found for cleanup")
            return
        
        temp_dir = sandbox_info["temp_dir"]
        container = sandbox_info.get("container")
        
        # Stop and remove container if it exists
        if container:
            try:
                container.stop()
                container.remove()
                debug(f"[SANDBOX] Stopped and removed container for <{sandbox_id}>")
            except Exception as e:
                warn(f"[SANDBOX] Could not clean up container for <{sandbox_id}>: {e}")
        
        # Clean up temp directory
        cleanup_temp_dir(temp_dir)
        del self.sandboxes[sandbox_id]
        
        debug(f"[SANDBOX] Cleaned up sandbox <{sandbox_id}>")

    def cleanup_all_sandboxes(self):
        """Clean up all active sandboxes."""
        for sandbox_id in list(self.sandboxes.keys()):
            self.cleanup_sandbox(sandbox_id)



    def _get_network_config(self, network_mode):
        if network_mode == SandboxNetworkMode.SANDBOX:
            return {"network": SANDBOX_NETWORK_NAME}
        elif network_mode == SandboxNetworkMode.PUBLIC:
            return {}
        elif network_mode == SandboxNetworkMode.BOTH:
            return {"network": SANDBOX_NETWORK_NAME}
        else:
            raise ValueError(f"Unknown network mode: {network_mode}")

    def _create_sandbox_network(self):
        """
        Create the isolated sandbox network.
        
        This network allows sandboxes to communicate with each other without going over the Internet.
        """

        try:
            self.docker.networks.get(SANDBOX_NETWORK_NAME)
            debug(f"[SANDBOX] Found sandbox network: {SANDBOX_NETWORK_NAME}")
        except docker.errors.NotFound:
            try:
                self.docker.networks.create(
                    SANDBOX_NETWORK_NAME,
                    driver="bridge",
                    internal=True
                )
                info(f"[SANDBOX] Created sandbox network: {SANDBOX_NETWORK_NAME}")
            except Exception as e:
                error(f"[SANDBOX] Failed to create sandbox network: {e}")
                raise
        except Exception as e:
            raise



    def _create_sandbox_proxy(self, gateway_url):
        """
        Spawn the sandbox proxy server.
        
        This is a special sandbox that runs a proxy server (nginx).
        This is the only sandbox that can access the internet.
        
        The other sandboxes cannot directly access the internet.
        They can, however, access it through this proxy server, which only allows specific requests.
        The requests sent to this proxy are forwarded to the gateway (specified when creating the SandboxManager).
        """
  
        info(f"[SANDBOX] Running sandbox proxy")
        
        # Run proxy in Docker container on both networks
        self.proxy_container = self.docker.containers.run(
            "sandbox-proxy-image",
            name=SANDBOX_PROXY_HOST,
            network=SANDBOX_NETWORK_NAME,
            environment={
                "GATEWAY_URL": gateway_url,
                "GATEWAY_HOST": gateway_url.split("://")[1].split(":")[0]
            },
            remove=False,
            detach=True
        )
        
        try:
            bridge_network = self.docker.networks.get("bridge")
            bridge_network.connect(self.proxy_container)
            debug("[SANDBOX] Connected sandbox proxy to bridge network")
        except Exception as e:
            warn(f"[SANDBOX] Failed to connect sandbox proxy to bridge network: {e}")

        time.sleep(1)
    


    def get_num_sandboxes(self):
        """Get the number of sandboxes."""
        return len(self.sandboxes)