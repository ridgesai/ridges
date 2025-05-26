#!/bin/bash
set -e
# Runs the validator neuron on the local Subtensor chain.
# If you want to use mock responses, you must set use_mock_responses = True in neurons/validator.py (the flag is false by default).
# Example command with mock responses: add --use-mock-responses

VENV_PATH="../deps/.venv"

# Activate venv
if [ -d "$VENV_PATH" ]; then
    source "$VENV_PATH/bin/activate"
else
    echo "[ERROR] Python virtual environment not found at $VENV_PATH. Please run setup-btcli.sh first."
    exit 1
fi

# Prompt for wallet name, hotkey, and netuid
read -p "Enter validator wallet name (default: validator): " validator_wallet
validator_wallet=${validator_wallet:-validator}
read -p "Enter validator hotkey (default: default): " validator_hotkey
validator_hotkey=${validator_hotkey:-default}
read -p "Enter netuid (default: 1): " netuid
netuid=${netuid:-1}

# Run the validator
python neurons/validator.py --netuid "$netuid" --subtensor.network ws://127.0.0.1:9945 --wallet.name "$validator_wallet" --wallet.hotkey "$validator_hotkey"

echo "Validator running." 