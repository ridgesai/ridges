import os
import json
import shutil
import time
import asyncio
import socket
import threading
import urllib.request
import urllib.error
import urllib.parse
from typing import List, Tuple
import docker
from pathlib import Path
from shared.logging_utils import get_logger

from validator.config import RIDGES_API_URL
from validator.utils.temp_files import create_temp_file
from validator.sandbox.validator import validate_sandbox_dir

# Set up logger
logger = get_logger(__name__)

# Get the current directory where this file is located
CURRENT_DIR = Path(__file__).parent.absolute()

# The path (on the host filesystem) to the main script that will run in the sandbox
MAIN_FILE = str(CURRENT_DIR / "Main.py")

# The Docker image to use for the sandbox
# docker build -t sandbox-runner .
SANDBOX_DOCKER_IMAGE = "sandbox-runner"

# The mounted directories/files (these paths exist only in the sandbox)
# The real paths are stored in the Sandbox object but the mounted paths are constant
SANDBOX_DIR = "/sandbox"
SANDBOX_MAIN_FILE = SANDBOX_DIR + "/Main.py"
SANDBOX_INPUT_FILE = SANDBOX_DIR + "/input.json"
SANDBOX_OUTPUT_FILE = SANDBOX_DIR + "/output.json"

# The mounted directories/files that come from the agent's submitted code
SANDBOX_SOURCE_DIR = SANDBOX_DIR + "/src"
SANDBOX_SOURCE_AGENT_MAIN_FILE = SANDBOX_SOURCE_DIR + "/agent.py" # NOTE: We don't actually mount this, we just expect that it exists

# The mounted directory that contains the repository that the agent is solving a problem for
SANDBOX_REPO_DIR = SANDBOX_DIR + "/repo"

# The maximum resource usage that is allowed for a sandbox
SANDBOX_MAX_CPU_USAGE = 70 # %
SANDBOX_MAX_RAM_USAGE = 512 * 4# MiB 
SANDBOX_MAX_RUNTIME = 20 * 60 # seconds

# The name of the network that the sandbox will be connected to
SANDBOX_NETWORK_NAME = "sandbox-network"

# Unix socket for secure host access
SOCKET_PATH = "/tmp/sandbox_proxy.sock"
ALLOWED_PATHS = {"/agents/embeddings", "/agents/inference"}

def start_proxy():
    """Start minimal Unix socket proxy with JSON protocol"""
    try:
        os.unlink(SOCKET_PATH)
    except OSError:
        pass

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(SOCKET_PATH)
    sock.listen(128)
    os.chmod(SOCKET_PATH, 0o666)

    logger.info(f"Unix socket proxy started on {SOCKET_PATH}")

    def handle_client(client):
        try:
            # Read JSON message from client
            data = b""
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                data += chunk
                # Try to parse as JSON to see if we have a complete message
                try:
                    json.loads(data.decode('utf-8'))
                    break  # Complete JSON message received
                except json.JSONDecodeError:
                    continue  # Keep reading

            if not data:
                client.send(json.dumps({"error": "No data received"}).encode('utf-8'))
                return

            try:
                message = json.loads(data.decode('utf-8'))
                endpoint = message.get("endpoint")
                request_data = message.get("data", {})

                logger.debug(f"Received JSON request: endpoint={endpoint}, data_size={len(str(request_data))}")

                if endpoint not in ALLOWED_PATHS:
                    client.send(json.dumps({"error": f"Endpoint {endpoint} not allowed"}).encode('utf-8'))
                    return

                # Build target URL
                target_url = RIDGES_API_URL + endpoint
                logger.debug(f"Forwarding request to: {target_url}")

                # Create HTTP request to the API
                req = urllib.request.Request(target_url, data=json.dumps(request_data).encode('utf-8'), method='POST')
                req.add_header('Content-Type', 'application/json')

                # Send request to API
                resp = urllib.request.urlopen(req)
                response_body = resp.read()

                # Parse the API response
                try:
                    api_response = json.loads(response_body.decode('utf-8'))
                    # Send JSON response back to client
                    client.send(json.dumps(api_response).encode('utf-8'))
                except json.JSONDecodeError:
                    # If API response is not JSON, wrap it
                    client.send(json.dumps({"response": response_body.decode('utf-8', errors='ignore')}).encode('utf-8'))

                logger.debug(f"Sent JSON response: {len(response_body)} bytes")

            except json.JSONDecodeError as e:
                client.send(json.dumps({"error": f"Invalid JSON: {str(e)}"}).encode('utf-8'))
            except urllib.error.URLError as e:
                client.send(json.dumps({"error": f"API request failed: {str(e)}"}).encode('utf-8'))
            except Exception as e:
                client.send(json.dumps({"error": f"Proxy error: {str(e)}"}).encode('utf-8'))
                logger.error(f"Proxy error: {e}")

        except Exception as e:
            error_response = json.dumps({"error": f"Unexpected error: {str(e)}"})
            client.send(error_response.encode('utf-8'))
            logger.error(f"Unexpected error in proxy: {e}")
        finally:
            client.close()

    def run():
        logger.info("Proxy server loop started")
        while True:
            try:
                client, addr = sock.accept()
                logger.debug(f"Accepted connection from {addr}")
                threading.Thread(target=handle_client, args=(client,), daemon=True).start()
            except Exception as e:
                logger.error(f"Error accepting connection: {e}")
                break

    proxy_thread = threading.Thread(target=run, daemon=True)
    proxy_thread.start()
    logger.info("Proxy thread started")

    # Give the proxy a moment to start up
    time.sleep(0.1)

    # Verify the socket is working
    try:
        test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        test_sock.connect(SOCKET_PATH)
        test_sock.close()
        logger.info("Proxy socket verification successful")
    except Exception as e:
        logger.error(f"Proxy socket verification failed: {e}")
        raise

    return sock

class Sandbox:
    swebench_instance_id: str
    manager: 'SandboxManager'
    _id_counter = 1


    def __init__(self, swebench_instance_id, manager: 'SandboxManager', src_dir: str, repo_dir_path: str):        
        self.swebench_instance_id = swebench_instance_id
        self.manager = manager

        self.id = Sandbox._id_counter
        Sandbox._id_counter += 1

        self.src_dir = src_dir
        self.repo_dir_path = repo_dir_path
        
        self.running = False
        
        self.success = None # None until run_*() is called, then True/False after the sandbox finishes running
        self.output = None # Set if success is True
        self.error = None # Set if success is False

        # Validate the sandbox directory
        try:
            validate_sandbox_dir(src_dir)
        except ValueError as e:
            self.success = False
            self.error = str(e)

    def run_sync(self, challenge: dict):
        self.running = True
        self._run(challenge)

    async def run_async(self, challenge: dict):
        self.running = True
        
        async def _async_main():
            logger.info(f'Running sandbox {self.id}')
            await asyncio.to_thread(self._run, challenge)
            if self.success:
                logger.info(f'Sandbox {self.id} ran successfully')
            else:
                logger.error(f'Sandbox {self.id} ran unsuccessfully, error: {self.error}')
        
        # Start the async task
        self._task = asyncio.create_task(_async_main())
    
    async def wait(self):
        if self.running and hasattr(self, '_task'):
            await self._task

    def _run(self, challenge: dict):
        if (self.success is not None):
            self.running = False
            return
        
        try:
            # Create an input and output file on the host filesystem
            self.input_file = create_temp_file()
            self.output_file = create_temp_file()

            # Write the input to the input file
            with open(self.input_file, 'w') as f:
                json.dump(challenge, f)

            # Create the Docker container, and run the Main.py script
            self.start_time = time.time()
            self.container = self.manager.docker.containers.run(
                image=SANDBOX_DOCKER_IMAGE,
                network=SANDBOX_NETWORK_NAME,
                volumes={
                    # Mount the appropriate files
                    os.path.abspath(MAIN_FILE): {"bind": SANDBOX_MAIN_FILE, "mode": "ro"},
                    self.input_file: {"bind": SANDBOX_INPUT_FILE, "mode": "ro"},
                    self.output_file: {"bind": SANDBOX_OUTPUT_FILE, "mode": "rw"},

                    # Mount the source directory
                    os.path.abspath(self.src_dir): {"bind": SANDBOX_SOURCE_DIR, "mode": "ro"},
                    
                    # Mount the Unix socket for secure host access
                    SOCKET_PATH: {"bind": "/tmp/sandbox_proxy.sock", "mode": "rw"},
                },
                working_dir=SANDBOX_DIR,
                detach=True
            )

            # Wait for the container to finish running, then kill it
            self.container.wait()
            self.container.remove()

            # An error might have occurred on another thread (e.g., the monitor thread killed the sandbox because it exceeded a resource limit)
            if (self.error):
                self.success = False
            else:
                with open(self.output_file, 'r') as f:
                    output = json.load(f)
                    if (output.get('success')):
                        self.success = True
                        self.output = output.get('output')
                    else:
                        self.success = False
                        self.error = output.get('error')

            # Delete the input and output files
            os.remove(self.input_file)
            os.remove(self.output_file)
        except Exception as e:
            self.success = False
            self.error = str(e)
        
        self.running = False
    
    def cleanup(self):
        shutil.rmtree(self.src_dir, ignore_errors=True)



class SandboxManager:
    sandboxes: List[Sandbox]

    def __init__(self):
        # Connect to the locally running Docker daemon
        self.docker = docker.from_env()

        # Ensure the sandbox-runner image is built
        self._ensure_sandbox_image()

        # Create the sandbox network if it doesn't exist
        # TODO
        try:
            self.docker.networks.get(SANDBOX_NETWORK_NAME)
        except docker.errors.NotFound:
            self.docker.networks.create(SANDBOX_NETWORK_NAME, driver='bridge', internal=False)
        
        # Initialize and start the Unix socket proxy
        logger.info("Starting Unix socket proxy...")
        self.unix_proxy = start_proxy()
        logger.info("Unix socket proxy started successfully")
        
        # Verify the socket file exists and is accessible
        if os.path.exists(SOCKET_PATH):
            logger.info(f"Socket file exists: {SOCKET_PATH}")
            try:
                import stat
                st = os.stat(SOCKET_PATH)
                logger.info(f"Socket file permissions: {oct(st.st_mode)}")
                logger.info(f"Socket file owner: {st.st_uid}")
            except Exception as e:
                logger.error(f"Error checking socket file: {e}")
        else:
            logger.error(f"Socket file does not exist: {SOCKET_PATH}")
            raise RuntimeError("Socket file was not created")
        
        logger.info("Unix socket proxy setup complete")
            
        self.sandboxes = []
        # Start the monitor as an asyncio task
        self._monitor_task = asyncio.create_task(self._monitor())
    
    async def _monitor(self):
        while True:
            for sandbox in self.sandboxes:
                if sandbox.running and sandbox.success is None:
                    try:
                        await asyncio.to_thread(self._monitor_sandbox, sandbox)
                    except Exception:
                        continue
            
            await asyncio.sleep(1)

    def _monitor_sandbox(self, sandbox):
        # Get the stats
        stats = sandbox.container.stats(stream=False)
        
        cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        system_delta = stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
        
        sandbox.cpu_usage = cpu_delta / system_delta * 100
        sandbox.ram_usage = stats["memory_stats"]["usage"] / (1024 * 1024)

        current_time = time.time()
        runtime = current_time - sandbox.start_time

        # logger.debug(f'sandbox {sandbox.id}: cpu: {sandbox.cpu_usage:.1f}, ram: {sandbox.ram_usage:.1f} MiB, runtime: {runtime:.1f} seconds') 
        
        # Check if the sandbox is using too many resources, if so, kill it and set the error
        if sandbox.cpu_usage > SANDBOX_MAX_CPU_USAGE:
            sandbox.error = 'CPU limit exceeded'
            sandbox.container.kill()
            logger.warning(f'Killed sandbox {sandbox.id} because CPU limit exceeded')
        elif sandbox.ram_usage > SANDBOX_MAX_RAM_USAGE:
            sandbox.error = 'RAM limit exceeded'
            sandbox.container.kill()
            logger.warning(f'Killed sandbox {sandbox.id} because RAM limit exceeded')
        elif runtime > SANDBOX_MAX_RUNTIME:
            sandbox.error = 'Runtime limit exceeded'
            sandbox.container.kill()
            logger.warning(f'Killed sandbox {sandbox.id} because runtime limit exceeded')
        
    def add_sandbox(self, swebench_instance_id: str, src_dir: str, repo_dir_path: str):
        sandbox = Sandbox(swebench_instance_id=swebench_instance_id, manager=self, src_dir=src_dir, repo_dir_path=repo_dir_path)
        self.sandboxes.append(sandbox)
        return sandbox

    async def wait_for_all_sandboxes(self):
        for sandbox in self.sandboxes:
            await sandbox.wait()
    
    def get_patches_and_errors(self) -> List[Tuple[bool, str, str, str]]:
        patches_and_errors = []
        for sandbox in self.sandboxes:
            if sandbox.success:
                patches_and_errors.append((True,sandbox.swebench_instance_id, sandbox.output.get("patch"), None))
            else:
                patches_and_errors.append((False, sandbox.swebench_instance_id, None, sandbox.error))
        return patches_and_errors
    
    def cleanup(self):
        # Stop the Unix socket proxy
        if hasattr(self, 'unix_proxy'):
            try:
                self.unix_proxy.close()
            except:
                pass
        
        for sandbox in self.sandboxes:
            sandbox.cleanup()
            self.sandboxes.remove(sandbox)

    def _ensure_sandbox_image(self):
        """Check if the sandbox-runner image exists and build it if not."""
        try:
            # Try to get the image
            self.docker.images.get(SANDBOX_DOCKER_IMAGE)
            logger.info(f"Docker image '{SANDBOX_DOCKER_IMAGE}' already exists")
        except docker.errors.ImageNotFound:
            logger.info(f"Docker image '{SANDBOX_DOCKER_IMAGE}' not found, building...")
            try:
                # Build the image from the Dockerfile in the sandbox directory
                dockerfile_path = str(CURRENT_DIR / "Dockerfile")
                logger.info(f"Building image from {dockerfile_path}")
                
                self.docker.images.build(
                    path=str(CURRENT_DIR),
                    dockerfile="Dockerfile",
                    tag=SANDBOX_DOCKER_IMAGE,
                    rm=True  # Remove intermediate containers
                )
                
                logger.info(f"Successfully built Docker image '{SANDBOX_DOCKER_IMAGE}'")
            except Exception as e:
                logger.error(f"Failed to build Docker image '{SANDBOX_DOCKER_IMAGE}': {e}")
                raise
