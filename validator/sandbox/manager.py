import os
import json
import shutil
import time
from typing import List, Tuple
import docker
import threading
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
SANDBOX_MAX_CPU_USAGE = 5 # %
SANDBOX_MAX_RAM_USAGE = 256 # MiB 
SANDBOX_MAX_RUNTIME = 20 * 60 # seconds

# The name of the network that the sandbox will be connected to
SANDBOX_NETWORK_NAME = "sandbox-network"



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

    def run_async(self, challenge: dict):
        self.done_event = threading.Event()
        
        def _thread_main():
            logger.info(f'Running sandbox {self.id}')
            self._run(challenge)
            if self.success:
                logger.info(f'Sandbox {self.id} ran successfully')
            else:
                logger.error(f'Sandbox {self.id} ran unsuccessfully, error: {self.error}')
            self.done_event.set()
        
        self.running = True
        thread = threading.Thread(target=_thread_main, daemon=True)
        thread.start()
    
    def wait(self):
        if (self.running):
            self.done_event.wait()

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
                volumes={
                    # Mount the appropriate files
                    os.path.abspath(MAIN_FILE): {"bind": SANDBOX_MAIN_FILE, "mode": "ro"},
                    self.input_file: {"bind": SANDBOX_INPUT_FILE, "mode": "ro"},
                    self.output_file: {"bind": SANDBOX_OUTPUT_FILE, "mode": "rw"},

                    # Mount the source directory
                    os.path.abspath(self.src_dir): {"bind": SANDBOX_SOURCE_DIR, "mode": "ro"},
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

        # Create the sandbox network if it doesn't exist
        # TODO
        try:
            self.docker.networks.get(SANDBOX_NETWORK_NAME)
        except docker.errors.NotFound:
            self.docker.networks.create(SANDBOX_NETWORK_NAME, driver='bridge', internal=True)
            
        self.sandboxes = []
        threading.Thread(target=self._monitor, daemon=True).start()
    
    def _monitor(self):
        while True:
            for sandbox in self.sandboxes:
                if sandbox.running and sandbox.success is None:
                    try:
                        self._monitor_sandbox(sandbox)
                    except Exception:
                        continue
            
            time.sleep(1)

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

    def wait_for_all_sandboxes(self):
        for sandbox in self.sandboxes:
            sandbox.wait()
    
    def get_successful_patches(self) -> List[Tuple[str, str]]:
        patches = []
        for sbox in self.sandboxes:
            if sbox.success:
                patches.append((sbox.swebench_instance_id, sbox.output.get("patch")))
            else:
                logger.error(f"Sandbox for instance {sbox.swebench_instance_id} failed: {sbox.error}")
        return patches
    
    def cleanup(self):
        for sandbox in self.sandboxes:
            sandbox.cleanup()
            self.sandboxes.remove(sandbox)
