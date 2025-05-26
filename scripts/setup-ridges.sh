#!/bin/bash
set -e
# Sets up the ridges repo and SWE-agent, prepares .env, and pulls the latest sweagent Docker image.
# Assumes you are already in the ridges repo directory.

# Ensure we are in the 'ridges' directory
if [ "$(basename "$PWD")" != "ridges" ]; then
    echo "[ERROR] Please run this script from the 'ridges' directory."
    exit 1
fi

# Check for deps/.venv and deps/bittensor (from setup-btcli.sh)
if [ ! -d "../deps/.venv" ] || [ ! -d "../deps/bittensor" ]; then
    echo "[ERROR] Required dependencies not found. Please run setup-btcli.sh first."
    exit 1
fi

# Path to shared venv (created by setup-btcli.sh)
VENV_PATH="../deps/.venv"

# Activate venv
if [ -d "$VENV_PATH" ]; then
    source "$VENV_PATH/bin/activate"
else
    echo "[ERROR] Python virtual environment not found at $VENV_PATH. Please run setup-btcli.sh first."
    exit 1
fi

# Install SWE-agent and ridges in editable mode
pip install -e SWE-agent -e .

echo "[INFO] Installed SWE-agent and ridges in editable mode."

# Set up .env file if not present
if [ ! -f .env ]; then
    if [ -f .env.miner_example ]; then
        cp .env.miner_example .env
        echo "[INFO] .env file created from .env.miner_example."
    elif [ -f .env.validator_example ]; then
        cp .env.validator_example .env
        echo "[INFO] .env file created from .env.validator_example."
    else
        echo "[WARNING] No .env example file found. Please create a .env file manually."
    fi
else
    echo "[INFO] .env file already exists."
fi

echo "[ACTION REQUIRED] Please edit the .env file and add your API keys (e.g., OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)."
echo "Once you have updated the .env file, press Enter to continue."
read -r

echo "[INFO] Pulling the latest sweagent Docker image..."
docker pull sweagent/swe-agent:latest

echo "[INFO] ridges setup complete." 