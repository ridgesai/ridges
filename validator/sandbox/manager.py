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
SANDBOX_MAX_CPU_USAGE = 50 # %
SANDBOX_MAX_RAM_USAGE = 512 * 4# MiB 
SANDBOX_MAX_RUNTIME = 20 * 60 # seconds

# The name of the network that the sandbox will be connected to
SANDBOX_NETWORK_NAME = "sandbox-network"

# Unix socket for secure host access
SOCKET_PATH = "/tmp/sandbox_proxy.sock"
ALLOWED_PATHS = {"/agents/embeddings", "/agents/inference"}

def start_proxy():
    """Start minimal Unix socket proxy"""
    try:
        os.unlink(SOCKET_PATH)
    except OSError:
        pass
    
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(SOCKET_PATH)
    sock.listen(128)
    os.chmod(SOCKET_PATH, 0o666)
    
    def handle_client(client):
        try:
            # Read the entire HTTP request – keep reading until we have received
            # the number of bytes advertised in the *Content-Length* header.
            buf = bytearray()
            header_end = None
            content_len = None

            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)

                # Once we have the blank line we can parse headers and figure
                # out how many body bytes to expect.
                if header_end is None and b"\r\n\r\n" in buf:
                    header_end = buf.find(b"\r\n\r\n")
                    header_lines = buf[:header_end].decode(errors="ignore").split("\r\n")[1:]
                    for line in header_lines:
                        if ":" in line:
                            k, v = line.split(":", 1)
                            if k.strip().lower() == "content-length":
                                try:
                                    content_len = int(v.strip())
                                except ValueError:
                                    content_len = 0
                    # If no Content-Length, assume no body.
                    if content_len is None:
                        content_len = 0

                # After headers parsed: stop once entire body received.
                if header_end is not None:
                    body_bytes = len(buf) - (header_end + 4)
                    if body_bytes >= content_len:
                        break

            data = buf.decode(errors="ignore")
            logger.debug(f"Received socket proxy request: {data}")
            
            # Parse HTTP request
            lines = data.split('\r\n')
            if not lines:
                return
                
            request_line = lines[0]
            parts = request_line.split()
            if len(parts) < 2:
                return
                
            method, full_path = parts[0], parts[1]
            path = full_path.split('?')[0]
            
            # Extract headers and body
            headers = {}
            body_start = -1
            for i, line in enumerate(lines[1:], 1):
                if line == '':
                    body_start = i + 1
                    break
                if ':' in line:
                    key, value = line.split(':', 1)
                    headers[key.strip().lower()] = value.strip()
            
            # Get request body if present
            request_body = None
            if body_start > 0 and body_start < len(lines):
                request_body = '\r\n'.join(lines[body_start:])
            
            logger.debug(f"Method: {method}, Path: {path}")
            logger.debug(f"Headers: {headers}")
            logger.debug(f"Body: {request_body}")
            
            if path in ALLOWED_PATHS:
                # Build target URL
                target_url = f"http://localhost:8000{full_path}"
                
                # Create request
                if method.upper() == 'POST' and request_body:
                    req = urllib.request.Request(target_url, data=request_body.encode(), method='POST')
                    req.add_header('Content-Type', 'application/json')
                    with open('conversation.jsonc', 'a') as f:
                        f.write(json.loads(request_body).get("input_text"))
                        f.write(",\n")
                else:
                    req = urllib.request.Request(target_url, method=method)

                
                # Add original headers
                for key, value in headers.items():
                    if key not in ['host', 'connection']:
                        req.add_header(key, value)
                
                # Send request
                resp = urllib.request.urlopen(req)
                body = resp.read()

                with open('conversation.jsonc', 'a') as f:
                    f.write(body.decode('utf-8', errors='ignore'))
                    f.write(",\n")
                
                # Send proper HTTP response
                response = f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
                logger.debug(f"Sending socket proxy response: {len(body)} bytes")
                client.send(response)
            else:
                client.send(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        except Exception as e:
            error_msg = f"HTTP/1.1 500 Error\r\nContent-Length: 0\r\n\r\n"
            logger.error(f"Error sending socket proxy response: {error_msg}")
            logger.error(f"Error: {e}")
            client.send(error_msg.encode())
        finally:
            client.close()
    
    def run():
        while True:
            try:
                client, _ = sock.accept()
                threading.Thread(target=handle_client, args=(client,), daemon=True).start()
            except:
                break
    
    threading.Thread(target=run, daemon=True).start()
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
        self.unix_proxy = start_proxy()
            
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
            self.unix_proxy.close()
        
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
