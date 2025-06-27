#!/bin/bash

# Parse arguments
PM2_PROCESS_NAME=""
SKIP_CONFIRM_ENV=false

# Parse all arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --help)
            echo "Usage: $0 [OPTIONS] [PM2_PROCESS_NAME]"
            echo "  PM2_PROCESS_NAME: The name of the PM2 process to start. Defaults to ridges-validator."
            echo "  --skip-confirm-env: Skip the environment variable confirmation step"
            echo "  --help: Show this help message"
            exit 0
            ;;
    esac
done

# Set default PM2 process name if not provided
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
        read -p "Virtual environment not found at $VENV_PATH, run 'uv venv' to create it? (y/N) " confirm
        if [ "$confirm" = "y" ]; then
            uv venv
            source "$VENV_PATH/bin/activate"
        else
            exit 1
        fi
    fi
}

function is_validator_running() {
  pm2 list | grep "$PM2_PROCESS_NAME " | grep -v "ridges-validator " | grep -q "online"
}

# Activate virtual environment initially
activate_venv

# Run validator if not already running
if ! is_validator_running; then
    echo "No process named '$PM2_PROCESS_NAME' is running, starting one:"
    echo "(Add an argument to this script if you'd like to change the PM2 process name, e.g. $0 my-pm2-process-name)"
    echo ""
    # Exit if validator/.env does not exist
    if [ ! -f "validator/.env" ]; then
        echo "validator/.env does not exist, please create it. You can use validator/.env.example as a template."
        # Offer to copy the template
        read -p "Would you like to run 'cp validator/.env.example validator/.env'? (y/N) " confirm
        if [ "$confirm" = "y" ]; then
            cp validator/.env.example validator/.env
            echo "validator/.env.example copied to validator/.env"
        fi
        exit 1
    fi
    
    uv pip install -e "."
    pm2 start uv  --name $PM2_PROCESS_NAME -- run validator/main.py
else
    # Get PM2 process ID
    PM2_ID=$(pm2 id $PM2_PROCESS_NAME)
    echo "✅ $PM2_PROCESS_NAME is already running with PM2 ID: $PM2_ID. Auto-update enabled, checking for updates periodically"
fi

while true; do
    echo -n "$(date '+%Y-%m-%d %H:%M:%S') - Checking for updates from $(git remote get-url origin) - "

    VERSION=$(git rev-parse HEAD)
    git pull --rebase --autostash
    NEW_VERSION=$(git rev-parse HEAD)

    if [ $VERSION != $NEW_VERSION ]; then
        echo "Code updated on branch $(git rev-parse --abbrev-ref HEAD)"
        echo "Latest commit: $(git log -1 --pretty=%B)"
        echo "Reinstalling dependencies if necessary..."
        activate_venv
        uv pip install -e "."
        pm2 restart $PM2_PROCESS_NAME --update-env
        echo "Update completed at $(date '+%Y-%m-%d %H:%M:%S'), validator is now running on version $NEW_VERSION"
    else
        echo "    No updates found, sleeping for 5 minutes"
    fi
    sleep 5m
done