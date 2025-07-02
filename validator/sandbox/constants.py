from pathlib import Path

# Get the current directory where this file is located
CURRENT_DIR = Path(__file__).parent.absolute()

# The path (on the host filesystem) to the main script that will run in the sandbox
MAIN_FILE = str(CURRENT_DIR / "agent_runner.py")

# The Docker image to use for the sandbox
# docker build -t sandbox-runner .
SANDBOX_DOCKER_IMAGE = "sandbox-runner"

# The mounted directories/files (these paths exist only in the sandbox)
# The real paths are stored in the Sandbox object but the mounted paths are constant
SANDBOX_DIR = "/sandbox"
SANDBOX_MAIN_FILE = SANDBOX_DIR + "/agent_runner.py"
SANDBOX_INPUT_FILE = SANDBOX_DIR + "/input.json"
SANDBOX_OUTPUT_FILE = SANDBOX_DIR + "/output.json"

# The mounted directory that contains the repository that the agent is solving a problem for
SANDBOX_REPO_DIR = SANDBOX_DIR + "/repo"

# The mounted directories/files that come from the agent's submitted code
SANDBOX_SOURCE_DIR = SANDBOX_DIR + "/src"
SANDBOX_SOURCE_AGENT_MAIN_FILE = SANDBOX_SOURCE_DIR + "/agent.py" # NOTE: We don't actually mount this, we just expect that it exists

# The maximum resource usage that is allowed for a sandbox
SANDBOX_MAX_RAM_USAGE = 512 * 4 # MiB 
SANDBOX_MAX_RUNTIME = 20 * 60 # seconds

# The name of the network that the sandbox will be connected to
SANDBOX_NETWORK_NAME = "sandbox-network"

# Nginx proxy image & container details
PROXY_DOCKER_IMAGE = "sandbox-nginx-proxy"
PROXY_CONTAINER_NAME = "sandbox-proxy"

# Directory to cache cloned repositories for reuse across validations
# Repositories will be stored at validator/repos/<org>/<repo>
REPOS_BASE_DIR = Path(__file__).parent.parent / "repos"
REPO_CACHE_DIR = Path(__file__).parent.parent / "repo_cache"
AGENTS_BASE_DIR = Path(__file__).parent.parent / "agents" 