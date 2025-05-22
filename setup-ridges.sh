#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored messages
print_message() {
  echo -e "${BLUE}[RIDGES SETUP]${NC} $1"
}

print_success() {
  echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
  echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if a command exists
check_command() {
  if ! command -v $1 &> /dev/null; then
    return 1
  else
    return 0
  fi
}

# Function to check Python version
check_python_version() {
  if check_command python3; then
    python_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    if [[ $python_version == 3.9* ]] || [[ $python_version == 3.10* ]] || [[ $python_version == 3.11* ]]; then
      print_success "Python $python_version detected."
      return 0
    else
      print_warning "Python $python_version detected. Ridges requires Python 3.9-3.11."
      return 1
    fi
  else
    print_error "Python 3 not found."
    return 1
  fi
}

# Function to check for Rust and Cargo
check_rust() {
  if check_command rustc && check_command cargo; then
    print_success "Rust and Cargo detected."
    return 0
  else
    print_error "Rust and/or Cargo not found."
    return 1
  fi
}

# Function to check for Docker
check_docker() {
  if check_command docker; then
    # Check if Docker daemon is running
    if docker info &> /dev/null; then
      print_success "Docker detected and running."
      return 0
    else
      print_error "Docker is installed but not running."
      return 1
    fi
  else
    print_error "Docker not found."
    return 1
  fi
}

# Install dependencies based on OS
install_dependencies() {
  if [[ "$OSTYPE" == "darwin"* ]]; then
    print_message "macOS detected. Installing dependencies with Homebrew..."
    
    # Check if brew is installed
    if ! check_command brew; then
      print_error "Homebrew not found. Please install Homebrew first."
      print_message "Run this command to install Homebrew:"
      echo '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
      exit 1
    fi
    
    # Install dependencies
    brew install make git curl openssl llvm protobuf libusb
  elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    print_message "Linux detected. Installing dependencies..."
    
    # Check package manager
    if check_command apt-get; then
      sudo apt-get update
      sudo apt-get install -y build-essential git curl openssl llvm libprotobuf-dev libusb-1.0-0-dev
    elif check_command yum; then
      sudo yum install -y make git curl openssl llvm protobuf-devel libusbx-devel
    else
      print_error "Unsupported Linux distribution. Please install the following packages manually: make, git, curl, openssl, llvm, protobuf, libusb"
      exit 1
    fi
  else
    print_error "Unsupported operating system: $OSTYPE"
    exit 1
  fi
  
  print_success "Dependencies installed successfully."
}

# Setup Bittensor and Subtensor
setup_bittensor() {
  print_message "Setting up Bittensor and Subtensor..."
  
  # Clone subtensor repo if it doesn't exist
  if [ ! -d "subtensor" ]; then
    print_message "Cloning subtensor repository..."
    git clone https://github.com/opentensor/subtensor.git
  fi
  
  ./subtensor/scripts/init.sh

  # Setup subtensor
  cd subtensor
  git checkout main
  git pull
  
  # Install Bittensor CLI and Python SDK
  print_message "Installing Bittensor CLI and Python SDK..."
  pip install bittensor
  
 
  
  cargo build -p node-subtensor --profile release
  
  BUILD_BINARY=0 ./scripts/localnet.sh False
  
  
#   # Check if there's an existing subtensor process running
#   if pgrep -f "node-subtensor" > /dev/null; then
#     print_warning "Existing subtensor process found. Killing it..."
#     pkill -f "node-subtensor"
#     sleep 5
#   fi
  


#   # Try using the direct node executable first
#   if [ -f "target/release/node-subtensor" ]; then
#     print_message "Starting subtensor with direct node executable..."
#     ./target/release/node-subtensor --dev --tmp --rpc-cors all --unsafe-rpc-external --execution wasm &
#     SUBTENSOR_PID=$!
#     print_message "Subtensor started with PID: $SUBTENSOR_PID"
#   elif [ -f "scripts/localnet.sh" ]; then
#     print_message "Starting subtensor with localnet script..."
#     # Use the localnet script with the POW faucet option
#     ./scripts/localnet.sh True &
#     SUBTENSOR_PID=$!
#     print_message "Subtensor started with PID: $SUBTENSOR_PID"
#   else
#     print_error "Could not find a way to start subtensor. Please start it manually."
#     exit 1
#   fi
  
#   # Wait for the blockchain to start and verify it's running
#   print_message "Waiting for Subtensor to start (30 seconds)..."
  
#   # Sleep for initial startup
#   sleep 15
  
  # Check if blockchain is accessible
  attempts=0
  max_attempts=10
  success=false
  
  while [ $attempts -lt $max_attempts ]; do
    attempts=$((attempts + 1))
    print_message "Checking if subtensor is running (attempt $attempts/$max_attempts)..."
    
    if curl -s -H "Content-Type: application/json" -d '{"id":1, "jsonrpc":"2.0", "method":"system_health", "params":[]}' http://localhost:9944 | grep -q "result"; then
      print_success "Subtensor is running and accessible!"
      success=true
      break
    fi
    
    print_warning "Subtensor not responding yet. Waiting 5 more seconds..."
    sleep 5
  done
  
  if [ "$success" = false ]; then
    print_error "Failed to start subtensor after $max_attempts attempts. Please check the logs and try starting it manually."
    exit 1
  fi
  
  cd ..
}

# Function to verify connection to the blockchain
verify_chain_connection() {
  local attempts=0
  local max_attempts=5
  local success=false
  
  while [ $attempts -lt $max_attempts ]; do
    attempts=$((attempts + 1))
    print_message "Verifying connection to subtensor (attempt $attempts/$max_attempts)..."
    
    if btcli wallet list --subtensor.chain_endpoint ws://127.0.0.1:9945 &>/dev/null; then
      print_success "Successfully connected to subtensor!"
      return 0
    fi
    
    print_warning "Could not connect to subtensor. Waiting 5 more seconds..."
    sleep 5
  done
  
  print_error "Failed to connect to subtensor after $max_attempts attempts."
  return 1
}

# Setup Ridges repository
setup_ridges() {
  print_message "Setting up Ridges repository..."
  
  # Get the current directory
  current_dir=$(pwd)
  repo_dir_name=$(basename "$current_dir")
  
  # Check if we're already in the ridges repository
  if [ "$repo_dir_name" = "ridges" ]; then
    print_message "Already in ridges repository."
    ridges_dir="$current_dir"
  else
    # Clone Ridges repo if it doesn't exist
    if [ ! -d "ridges" ]; then
      print_message "Cloning Ridges repository..."
      git clone https://github.com/ridgesai/ridges.git
    fi
    cd ridges
    ridges_dir=$(pwd)
  fi
  
  # Initialize submodules
  print_message "Initializing git submodules..."
  git submodule update --init --recursive
  
  # Create and activate virtual environment
  print_message "Setting up Python virtual environment..."
  if [ ! -d ".venv" ]; then
    print_message "Creating virtual environment..."
    python3 -m venv .venv
  else
    print_message "Virtual environment already exists."
  fi
  
  # Activate virtual environment
  print_message "Activating virtual environment..."
  source .venv/bin/activate
  
  # Install Ridges
  print_message "Installing Ridges..."
  # Check if setup.py exists
  if [ -f "setup.py" ]; then
    pip install -e .
  else
    print_warning "setup.py not found. Skipping direct installation."
  fi
  
  # Install SWE-agent
  print_message "Installing SWE-agent..."
  if [ -d "SWE-agent" ]; then
    # Install SWE-agent if it exists
    cd SWE-agent
    if [ -f "requirements.txt" ]; then
      pip install -r requirements.txt
    fi
    if [ -f "pyproject.toml" ]; then
      pip install -e .
    fi
    cd "$ridges_dir"
  else
    print_warning "SWE-agent directory not found. Skipping installation."
  fi
  
  # Check and fix requirements
  print_message "Checking for additional requirements..."
  
  # Try different possible locations for the API requirements
  if [ -d "SWE-agent" ] && [ -f "SWE-agent/sweagent/api/requirements.txt" ]; then
    print_message "Installing API requirements..."
    
    # Fix the decorator import in requirements.txt if needed
    if grep -q "decorator @" SWE-agent/sweagent/api/requirements.txt; then
      sed -i.bak 's|decorator @ .*|decorator>=5.1.1|' SWE-agent/sweagent/api/requirements.txt
    fi
    
    # Fix SWEbench version if needed
    if grep -q "swebench" SWE-agent/sweagent/api/requirements.txt; then
      sed -i.bak 's|swebench.*|swebench>=4.0.3|' SWE-agent/sweagent/api/requirements.txt
    fi
    
    pip install -r SWE-agent/sweagent/api/requirements.txt
  fi
  
  # Always install modal
  print_message "Installing modal..."
  pip install "modal>=0.57.0"
  
  # Fix the import in swe_env.py if it exists
  if [ -d "SWE-agent" ] && [ -f "SWE-agent/sweagent/environment/swe_env.py" ]; then
    print_message "Fixing import in swe_env.py..."
    if grep -q "from swebench.harness.utils import" SWE-agent/sweagent/environment/swe_env.py; then
      sed -i.bak 's|from swebench.harness.utils import get_environment_yml, get_requirements|from swebench.harness.test_spec.python import get_environment_yml, get_requirements|' SWE-agent/sweagent/environment/swe_env.py
    fi
  fi
  
  # Try to find and update use_mock_responses in different possible locations
  print_message "Setting use_mock_responses to True in neurons files..."
  
  # Check for miner.py in different locations
  if [ -f "neurons/miner.py" ]; then
    sed -i.bak 's|\(.*use_mock_responses=\)False|\1True|' neurons/miner.py
  fi
  
  # Check for validator.py in different locations
  if [ -f "neurons/validator.py" ]; then
    sed -i.bak 's|\(.*use_mock_responses=\)False|\1True|' neurons/validator.py
  fi
  
  print_success "Ridges repository setup completed."
}

# Function to wait for user confirmation
wait_for_confirmation() {
  echo -e "${YELLOW}Press Enter to continue...${NC}"
  read
}

# Setup wallets
setup_wallets() {
  print_message "Setting up wallets..."
  
  # Configure btcli to allow partial stake
  print_message "Running btcli config set - set allow_partial_stake to true..."
  btcli config set
  
  # Create owner wallet (coldkey only)
  print_message "Creating owner coldkey - follow the prompts..."
  btcli wallet new_coldkey --wallet.name owner
  
  # Create owner hotkey
  print_message "Creating owner hotkey - follow the prompts..."
  btcli wallet new_hotkey --wallet.name owner --wallet.hotkey default
  
  # Create miner wallet (coldkey and hotkey)
  print_message "Creating miner coldkey - follow the prompts..."
  btcli wallet new_coldkey --wallet.name miner
  
  print_message "Creating miner hotkey - follow the prompts..."
  btcli wallet new_hotkey --wallet.name miner --wallet.hotkey default
  
  # Create validator wallet (coldkey and hotkey)
  print_message "Creating validator coldkey - follow the prompts..."
  btcli wallet new_coldkey --wallet.name validator
  
  print_message "Creating validator hotkey - follow the prompts..."
  btcli wallet new_hotkey --wallet.name validator --wallet.hotkey default
  
  # Mint tokens to owner
  print_message "Minting tokens to owner wallet..."
  btcli wallet faucet --wallet.name owner --subtensor.chain_endpoint ws://127.0.0.1:9945
  
  # Mint tokens to miner
  print_message "Minting tokens to miner wallet..."
  btcli wallet faucet --wallet.name miner --subtensor.chain_endpoint ws://127.0.0.1:9945
  
  # Mint tokens to validator
  print_message "Minting tokens to validator wallet..."
  btcli wallet faucet --wallet.name validator --subtensor.chain_endpoint ws://127.0.0.1:9945
  
  print_success "Wallets setup completed."
}

# Create subnet
create_subnet() {
  print_message "Creating subnet..."
  
  # Register subnet using owner wallet
  print_message "Registering subnet with owner wallet..."
  btcli subnet create --wallet.name owner --wallet.hotkey default --subtensor.chain_endpoint ws://127.0.0.1:9945
  
  # Ask user for the netuid
  print_message "Please enter the netuid of the subnet you just created (usually 1 for the first subnet):"
  read netuid
  
  # Add stake to the subnet
  print_message "Adding stake to subnet..."
  btcli stake add --wallet.name owner --wallet.hotkey default --subtensor.chain_endpoint ws://127.0.0.1:9945 --netuid $netuid --amount 100.0
  
  print_success "Subnet created with netuid $netuid."
  
  # Save the netuid for later use
  echo "$netuid" > .subnet_netuid
}

# Run validator and miner
run_services() {
  print_message "Starting Ridges services..."
  
  # Get the netuid
  if [ -f ".subnet_netuid" ]; then
    netuid=$(cat .subnet_netuid)
  else
    print_message "Please enter the netuid of your subnet:"
    read netuid
  fi
  
  # Ensure we're in the ridges directory
  current_dir=$(pwd)
  repo_dir_name=$(basename "$current_dir")
  
  if [ "$repo_dir_name" != "ridges" ] && [ -d "ridges" ]; then
    cd ridges
  fi
  
  # Ensure virtual environment is activated
  if [ -z "$VIRTUAL_ENV" ]; then
    if [ -f ".venv/bin/activate" ]; then
      source .venv/bin/activate
    fi
  fi
  
  # Run validator in background
  print_message "Starting validator..."
  print_message "Running this command:"
  VALIDATOR_CMD="python neurons/validator.py --netuid $netuid --subtensor.network ws://127.0.0.1:9945 --wallet.name validator --wallet.hotkey default --use-mock-responses > validator.log 2>&1 &"
  echo -e "${GREEN}$VALIDATOR_CMD${NC}"
  eval "$VALIDATOR_CMD"
  validator_pid=$!
  
  # Run miner in background
  print_message "Starting miner..."
  print_message "Running this command:"
  MINER_CMD="python neurons/miner.py --netuid $netuid --subtensor.network ws://127.0.0.1:9945 --wallet.name miner --wallet.hotkey default --use-mock-responses > miner.log 2>&1 &"
  echo -e "${GREEN}$MINER_CMD${NC}"
  eval "$MINER_CMD"
  miner_pid=$!
  
  print_success "Ridges services started."
  print_message "Validator PID: $validator_pid (logs in validator.log)"
  print_message "Miner PID: $miner_pid (logs in miner.log)"
  
  # Return to the original directory if we changed it
  if [ "$repo_dir_name" != "ridges" ] && [ -d "ridges" ]; then
    cd ..
  fi
}

# Main execution
main() {
  print_message "Welcome to Ridges Setup Script"
  print_message "This script will set up a complete Ridges development environment."
  
  # Check prerequisites
  print_message "Checking prerequisites..."
  
  # Check Python
  if ! check_python_version; then
    print_warning "Please install Python 3.9-3.11 before continuing."
    exit 1
  fi
  
  # Check Rust
  if ! check_rust; then
    print_warning "Installing Rust and Cargo..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
    source $HOME/.cargo/env
  fi
  
  # Check Docker
  if ! check_docker; then
    print_error "Docker is required but not running or not installed."
    print_message "Please install Docker Desktop (macOS/Windows) or Docker Engine (Linux) and start it before continuing."
    exit 1
  fi
  
  # Install dependencies
  install_dependencies
  
  # Setup components
  setup_bittensor
  setup_ridges
  
  # Verify connection to blockchain before continuing
  if ! verify_chain_connection; then
    print_error "Cannot proceed without a working blockchain connection."
    print_message "You can try starting the subtensor chain manually and then run this script again."
    exit 1
  fi
  
  setup_wallets
  create_subnet
  run_services
  
  print_success "Ridges development environment setup completed successfully!"
  print_message "You can now interact with your local Ridges environment."
  print_message "The Subtensor blockchain is running locally at ws://127.0.0.1:9945"
  print_message "The validator and miner are running in the background."
  print_message "To see validator logs: tail -f ridges/validator.log"
  print_message "To see miner logs: tail -f ridges/miner.log"
}

# Run the main function
main 