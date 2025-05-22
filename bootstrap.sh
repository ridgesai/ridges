#!/usr/bin/env bash
set -eo pipefail

# Ridges Development Environment Bootstrap Script
# This script sets up a complete Ridges development environment from scratch

# Print colored messages
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check if running with sudo (not recommended)
if [ "$EUID" -eq 0 ]; then
    log_warn "Running with sudo is not recommended. Please run as a regular user."
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Create workspace directory
WORKSPACE_DIR="$HOME/ridges-dev"
RIDGES_DIR="$WORKSPACE_DIR/ridges"
SUBTENSOR_DIR="$WORKSPACE_DIR/subtensor"

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check Python
    if command -v python3 &>/dev/null; then
        PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        if [[ ! "$PY_VER" =~ ^3\.(9|10|11)$ ]]; then
            log_error "Python version must be 3.9, 3.10, or 3.11. Found $PY_VER"
            log_info "Please install a compatible Python version."
            exit 1
        else
            log_info "Python $PY_VER detected."
        fi
    else
        log_error "Python 3 not found. Please install Python 3.9, 3.10, or 3.11"
        exit 1
    fi
    
    # Check pip
    if ! command -v pip3 &>/dev/null; then
        log_error "pip3 not found. Please install pip."
        exit 1
    fi
    
    # Check Rust/Cargo
    if ! command -v cargo &>/dev/null; then
        log_error "Rust/Cargo not found. Installing..."
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
        source "$HOME/.cargo/env"
    else
        log_info "Rust detected: $(cargo --version)"
    fi
    
    # Check Docker
    if ! command -v docker &>/dev/null; then
        log_error "Docker not found. Please install Docker."
        exit 1
    else
        log_info "Docker detected: $(docker --version)"
        # Check Docker service
        if ! docker info &>/dev/null; then
            log_error "Docker service is not running. Please start Docker."
            exit 1
        fi
    fi
    
    # Check Docker Compose
    if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null; then
        log_warn "Docker Compose not found. It's recommended for running multiple services."
    else
        log_info "Docker Compose detected."
    fi
    
    # Check PM2
    if ! command -v pm2 &>/dev/null; then
        log_warn "PM2 not found. Installing..."
        npm install -g pm2 || {
            log_error "Failed to install PM2. Make sure Node.js/npm is installed."
            exit 1
        }
    else
        log_info "PM2 detected: $(pm2 --version)"
    fi
    
    # Check Node.js
    if ! command -v node &>/dev/null; then
        log_warn "Node.js not found. It's required for PM2."
        log_info "Installing Node.js..."
        if [ "$(uname)" == "Darwin" ]; then
            # macOS
            if command -v brew &>/dev/null; then
                brew install node
            else
                log_error "Homebrew not found. Please install Node.js manually."
                exit 1
            fi
        elif [ "$(uname)" == "Linux" ]; then
            # Linux
            curl -sL https://deb.nodesource.com/setup_18.x | sudo -E bash -
            sudo apt-get install -y nodejs
        else
            log_error "Unsupported OS. Please install Node.js manually."
            exit 1
        fi
    else
        log_info "Node.js detected: $(node --version)"
    fi
    
    # Check jq
    if ! command -v jq &>/dev/null; then
        log_warn "jq not found. Installing..."
        if [ "$(uname)" == "Darwin" ]; then
            brew install jq
        elif [ "$(uname)" == "Linux" ]; then
            sudo apt-get install -y jq
        else
            log_error "Please install jq manually: https://stedolan.github.io/jq/download/"
            exit 1
        fi
    else
        log_info "jq detected: $(jq --version)"
    fi
    
    log_info "All prerequisites checked."
}

# Set up Python virtual environment
setup_venv() {
    log_info "Setting up Python virtual environment..."
    python3 -m pip install --upgrade pip
    python3 -m pip install virtualenv
    python3 -m virtualenv "$WORKSPACE_DIR/venv"
    source "$WORKSPACE_DIR/venv/bin/activate"
    log_info "Virtual environment created and activated."
}

# Clone repositories
clone_repos() {
    log_info "Cloning repositories..."
    mkdir -p "$WORKSPACE_DIR"
    
    # Clone Ridges
    if [ ! -d "$RIDGES_DIR" ]; then
        git clone https://github.com/ridgesai/ridges.git "$RIDGES_DIR"
        log_info "Ridges repository cloned."
    else
        log_info "Ridges repository already exists. Pulling latest changes..."
        cd "$RIDGES_DIR" && git pull
    fi
    
    # Clone Subtensor
    if [ ! -d "$SUBTENSOR_DIR" ]; then
        git clone https://github.com/opentensor/subtensor.git "$SUBTENSOR_DIR"
        log_info "Subtensor repository cloned."
    else
        log_info "Subtensor repository already exists. Pulling latest changes..."
        cd "$SUBTENSOR_DIR" && git pull
    fi
}

# Install Bittensor and dependencies
install_bittensor() {
    log_info "Installing Bittensor CLI and Python SDK..."
    source "$WORKSPACE_DIR/venv/bin/activate"
    
    # Install Bittensor
    python3 -m pip install bittensor==9.0.0
    
    # Install Ridges and its dependencies
    cd "$RIDGES_DIR"
    python3 -m pip install -e .
    
    log_info "Bittensor and Ridges installed successfully."
}

# Set up a local Subtensor
setup_local_subtensor() {
    log_info "Setting up local Subtensor blockchain..."
    export SUBTENSOR_ROOT="$SUBTENSOR_DIR"
    export RIDGES_ROOT="$RIDGES_DIR"
    
    # Set up environment variables in shell config
    SHELL_CONFIG="$HOME/.$(basename $SHELL)rc"
    if [ ! -f "$SHELL_CONFIG" ]; then
        # Try common alternatives
        if [ -f "$HOME/.bashrc" ]; then
            SHELL_CONFIG="$HOME/.bashrc"
        elif [ -f "$HOME/.zshrc" ]; then
            SHELL_CONFIG="$HOME/.zshrc"
        fi
    fi
    
    # Add env vars to shell config if not already present
    if [ -f "$SHELL_CONFIG" ]; then
        grep -q "SUBTENSOR_ROOT" "$SHELL_CONFIG" || echo "export SUBTENSOR_ROOT=\"$SUBTENSOR_DIR\"" >> "$SHELL_CONFIG"
        grep -q "RIDGES_ROOT" "$SHELL_CONFIG" || echo "export RIDGES_ROOT=\"$RIDGES_DIR\"" >> "$SHELL_CONFIG"
    fi
    
    # Start local subtensor
    log_info "Starting local Subtensor network..."
    cd "$SUBTENSOR_DIR"
    pm2 start -f "$SUBTENSOR_DIR/scripts/localnet.sh" --name localnet -- False --no-purge
    
    # Wait for blockchain to start
    log_info "Waiting for local blockchain to initialize (this may take 30-60 seconds)..."
    sleep 30
}

# Set up wallets and subnet
setup_wallets_and_subnet() {
    log_info "Setting up wallets and subnet..."
    source "$WORKSPACE_DIR/venv/bin/activate"
    
    # Run setup script from Ridges
    cd "$RIDGES_DIR"
    bash "$RIDGES_DIR/scripts/setup_staging_subnet.sh"
    
    log_info "Wallets and subnet set up successfully."
}

# Launch validators and miners
launch_services() {
    log_info "Launching Ridges validator and miner..."
    source "$WORKSPACE_DIR/venv/bin/activate"
    cd "$RIDGES_DIR"
    
    # Start validator
    pm2 start neurons/validator.py --name ridges-validator -- --netuid 1 --wallet.name validator --wallet.hotkey default --logging.debug
    
    # Start miner
    pm2 start neurons/miner.py --name ridges-miner -- --netuid 1 --wallet.name miner --wallet.hotkey default --logging.debug
    
    log_info "Services started successfully. Monitor with 'pm2 monit' or 'pm2 logs'"
}

# Create Docker Compose file
create_docker_compose() {
    log_info "Creating docker-compose.yml for easier management..."
    cat > "$WORKSPACE_DIR/docker-compose.yml" << EOF
version: '3'

services:
  subtensor:
    image: opentensor/subtensor:latest
    container_name: subtensor-local
    ports:
      - "9944:9944"
    volumes:
      - subtensor-data:/tmp/blockchain
    command: bash -c "/app/run_subtensor --no-validator --ws-external"
    environment:
      - SUBTENSOR_CHAIN_TYPE=local
    restart: unless-stopped

  validator:
    image: python:3.10-slim
    container_name: ridges-validator
    depends_on:
      - subtensor
    volumes:
      - bittensor-data:/root/.bittensor
      - ${RIDGES_DIR}:/app/ridges
    command: >
      bash -c "
        apt-get update && apt-get install -y git build-essential pkg-config curl &&
        pip install btcli &&
        cd /app/ridges && pip install -e . &&
        sleep 15 &&
        btcli config set --subtensor.network ws://subtensor:9944 &&
        btcli wallet create --n-words 12 --no-use-password --wallet-name validator --hotkey default --no-prompt || true &&
        btcli wallet faucet --wallet.name validator && 
        btcli subnet register --wallet.name validator --netuid 1 --wallet.hotkey default --no-prompt || true &&
        python3 neurons/validator.py --netuid 1 --wallet.name validator --wallet.hotkey default --logging.debug --subtensor.network ws://subtensor:9944
      "
    restart: unless-stopped

  miner:
    image: python:3.10-slim
    container_name: ridges-miner
    depends_on:
      - subtensor
      - validator
    volumes:
      - bittensor-data:/root/.bittensor
      - ${RIDGES_DIR}:/app/ridges
    command: >
      bash -c "
        apt-get update && apt-get install -y git build-essential pkg-config curl &&
        pip install btcli &&
        cd /app/ridges && pip install -e . &&
        sleep 30 &&
        btcli config set --subtensor.network ws://subtensor:9944 &&
        btcli wallet create --n-words 12 --no-use-password --wallet-name miner --hotkey default --no-prompt || true &&
        btcli wallet faucet --wallet.name miner &&
        btcli subnet register --wallet.name miner --netuid 1 --wallet.hotkey default --no-prompt || true &&
        python3 neurons/miner.py --netuid 1 --wallet.name miner --wallet.hotkey default --logging.debug --subtensor.network ws://subtensor:9944
      "
    restart: unless-stopped

volumes:
  subtensor-data:
  bittensor-data:
EOF

    log_info "docker-compose.yml created at $WORKSPACE_DIR/docker-compose.yml"
}

# Main function
main() {
    log_info "Starting Ridges development environment setup..."
    
    check_prerequisites
    
    # Create workspace directory
    mkdir -p "$WORKSPACE_DIR"
    
    # Check if this is being run non-interactively (e.g., piped from curl)
    if [ -t 0 ]; then
        # Running interactively, we can prompt the user
        echo -e "\nSelect setup type:"
        echo "1) Native setup (runs services directly on host)"
        echo "2) Docker setup (runs services in containers)"
        read -p "Enter your choice (1/2): " setup_type
    else
        # Non-interactive mode, use default (Docker)
        log_info "Running in non-interactive mode. Using default setup type: Docker"
        setup_type=2
    fi
    
    case "$setup_type" in
        1) # Native setup
            setup_venv
            clone_repos
            install_bittensor
            setup_local_subtensor
            setup_wallets_and_subnet
            launch_services
            ;;
        2) # Docker setup
            clone_repos
            create_docker_compose
            log_info "Starting Docker services..."
            cd "$WORKSPACE_DIR" && docker-compose up -d
            log_info "Note: First run may take several minutes as Docker images are being built."
            ;;
        *)
            log_error "Invalid choice. Exiting."
            exit 1
            ;;
    esac
    
    log_info "Setup complete! Ridges development environment is now ready."
    
    if [ "$setup_type" -eq "1" ]; then
        echo -e "\nTo monitor services: pm2 monit"
        echo "To view logs: pm2 logs"
        echo "To stop services: pm2 stop all"
    else
        echo -e "\nTo monitor Docker containers: docker-compose logs -f"
        echo "To stop containers: docker-compose down"
        echo -e "\nTo rebuild the Docker images: docker-compose build --no-cache"
    fi
    
    echo -e "\nEnjoy developing with Ridges!"
}

# Run main function
main 