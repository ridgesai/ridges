#!/bin/bash
set -e
# Registers the miner and validator on the subnet using btcli.
# Prompts for wallet names, hotkeys, and netuid.

VENV_PATH="../deps/.venv"

# Activate venv
if [ -d "$VENV_PATH" ]; then
    source "$VENV_PATH/bin/activate"
else
    echo "[ERROR] Python virtual environment not found at $VENV_PATH. Please run setup-btcli.sh first."
    exit 1
fi

# Check if subtensor chain is running (port 9945)
if ! nc -z localhost 9945; then
    echo "[ERROR] Subtensor chain is not running on port 9945. Please start the chain before running this script."
    exit 1
fi

# Prompt for netuid
read -p "Enter netuid for registration (default: 1): " netuid
netuid=${netuid:-1}

# Register miner
read -p "Enter miner wallet name (default: miner): " miner_wallet
miner_wallet=${miner_wallet:-miner}
read -p "Enter miner hotkey (default: default): " miner_hotkey
miner_hotkey=${miner_hotkey:-default}
echo "[INFO] Registering miner on subnet..."
btcli subnet register_miner --wallet.name "$miner_wallet" --wallet.hotkey "$miner_hotkey" --subtensor.chain_endpoint ws://127.0.0.1:9945 --netuid "$netuid"
if [ $? -eq 0 ]; then
    echo "[INFO] Miner registered successfully."
else
    echo "[ERROR] Miner registration failed."
    exit 1
fi

# Register validator
read -p "Enter validator wallet name (default: validator): " validator_wallet
validator_wallet=${validator_wallet:-validator}
read -p "Enter validator hotkey (default: default): " validator_hotkey
validator_hotkey=${validator_hotkey:-default}
echo "[INFO] Registering validator on subnet..."
btcli subnet register_validator --wallet.name "$validator_wallet" --wallet.hotkey "$validator_hotkey" --subtensor.chain_endpoint ws://127.0.0.1:9945 --netuid "$netuid"
if [ $? -eq 0 ]; then
    echo "[INFO] Validator registered successfully."
else
    echo "[ERROR] Validator registration failed."
    exit 1
fi 