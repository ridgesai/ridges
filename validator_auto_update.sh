#!/bin/bash

# Add a --help with instructions
if [ "$1" == "--help" ]; then
    echo "Usage: $0 [PM2_PROCESS_NAME]"
    echo "  PM2_PROCESS_NAME: The name of the PM2 process to start. Defaults to ridges-validator."
    exit 0
fi

PM2_PROCESS_NAME=$1
if [ -z "$PM2_PROCESS_NAME" ]; then
    PM2_PROCESS_NAME="ridges-validator"
fi
VENV_PATH=".venv"

# Ensure we're in the project root directory
cd "$(dirname "$0")"

# Function to activate virtual environment
activate_venv() {
    if [ -f "$VENV_PATH/bin/activate" ]; then
        source "$VENV_PATH/bin/activate"
    else
        echo "Virtual environment not found at $VENV_PATH"
        exit 1
    fi
}

function is_validator_running() {
  pm2 list | grep $PM2_PROCESS_NAME > /dev/null
}

# Activate virtual environment initially
activate_venv

# Run validator if not already running
if ! is_validator_running; then
    echo "Validator is not running, starting pm2 process '$PM2_PROCESS_NAME'"
    echo "Add an argument to this script to change the PM2 process name, e.g. $0 my-pm2-process-name"
    # Exit if validator/.env does not exist
    if [ ! -f "validator/.env" ]; then
        echo "validator/.env does not exist, please create it. You can use validator/.env.example as a template."
        exit 1
    fi

    # Make user confirm (y/N) if the fields in their validator/.env are correct (ignore lines that start with #)
    echo "Please confirm the following values in validator/.env are correct:"
    echo "  NETUID: $(grep -v '^#' validator/.env | grep NETUID | cut -d '=' -f2)"
    echo "  SUBTENSOR_NETWORK: $(grep -v '^#' validator/.env | grep SUBTENSOR_NETWORK | cut -d '=' -f2)"
    echo "  SUBTENSOR_ADDRESS: $(grep -v '^#' validator/.env | grep SUBTENSOR_ADDRESS | cut -d '=' -f2)"
    echo "  WALLET_NAME: $(grep -v '^#' validator/.env | grep WALLET_NAME | cut -d '=' -f2)"
    echo "  HOTKEY_NAME: $(grep -v '^#' validator/.env | grep HOTKEY_NAME | cut -d '=' -f2)"
    echo "  OPENAI_API_KEY: $(grep -v '^#' validator/.env | grep OPENAI_API_KEY | cut -d '=' -f2)"
    read -p "Are these values correct? (y/N) " confirm
    if [ "$confirm" != "y" ]; then
        echo "Please update the values in validator/.env and run this script again."
        exit 1
    fi
    uv pip install -e "."
    pm2 start uv  --name $PM2_PROCESS_NAME -- run validator/main.py
else
    echo "Validator is already running"
fi

while true; do
    sleep 5

    VERSION=$(git rev-parse HEAD)
    
    # Pull latest changes
    git pull --rebase --autostash origin main

    NEW_VERSION=$(git rev-parse HEAD)

    if [ $VERSION != $NEW_VERSION ]; then
        echo "Code updated on branch $(git rev-parse --abbrev-ref HEAD)"
        echo "Latest commit: $(git log -1 --pretty=%B)"
        echo "Reinstalling dependencies if necessary..."
        activate_venv
        uv pip install -e "."
        pm2 restart $PM2_PROCESS_NAME --update-env
        echo "Update completed at $(date '+%Y-%m-%d %H:%M:%S'), validator is now running on version $NEW_VERSION"
    fi
done