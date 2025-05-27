#!/usr/bin/env bash
set -e
# Registers a subnet on the local Subtensor chain using the owner wallet and optionally adds stake.
# Reference: https://github.com/opentensor/bittensor-subnet-template/blob/main/docs/running_on_staging.md#5-initialize
# NOTE: When run inside the Docker container, btcli is already on the PATH.

# Check if subtensor chain is running (port 9945)
if ! nc -z localhost 9945; then
    echo "[ERROR] Subtensor chain is not running on port 9945. Please start the chain before running this script."
    exit 1
fi

# Prompt for owner wallet name
read -p "Enter owner wallet name for subnet registration (default: owner): " owner_wallet
owner_wallet=${owner_wallet:-owner}

echo "[ACTION REQUIRED] You will be prompted for the netuid. Enter 1 for local devnet."
echo "If you need to specify a hotkey, you can add --wallet.hotkey <hotkey> to the command."
echo
btcli subnet register --wallet.name "$owner_wallet" --subtensor.chain_endpoint ws://127.0.0.1:9945

if [ $? -eq 0 ]; then
    echo "[INFO] Subnet registration complete."
else
    echo "[ERROR] Subnet registration failed."
    exit 1
fi

echo
read -p "Do you want to add stake to the owner wallet now? (y/n) [y]: " add_stake
add_stake=${add_stake:-y}
if [[ "$add_stake" =~ ^[Yy]$ ]]; then
    read -p "Enter hotkey for staking (default: default): " hotkey
    hotkey=${hotkey:-default}
    read -p "Enter amount to stake (in TAO): " amount
    if [ -z "$amount" ]; then
        echo "[ERROR] Amount is required to add stake."
        exit 1
    fi
    echo "[INFO] Adding $amount TAO stake to wallet '$owner_wallet' (hotkey: $hotkey)..."
    btcli stake add --wallet.name "$owner_wallet" --wallet.hotkey "$hotkey" --amount "$amount" --subtensor.chain_endpoint ws://127.0.0.1:9945
    if [ $? -eq 0 ]; then
        echo "[INFO] Stake added successfully."
    else
        echo "[ERROR] Failed to add stake."
        exit 1
    fi
else
    echo "[INFO] Skipping stake addition."
fi
