#!/usr/bin/env bash
set -e
# Interactive script to set up Bittensor wallets for owner, miner, and validator roles.
# References:
# https://docs.bittensor.com/working-with-keys
# https://docs.bittensor.com/getting-started/wallets
# NOTE: When run inside the Docker container, btcli is already on the PATH.

# Check if subtensor chain is running (port 9945)
if ! nc -z localhost 9945; then
    echo "[ERROR] Subtensor chain is not running on port 9945. Please start the chain before running this script."
    exit 1
fi

# Function to create a wallet (coldkey + hotkey)
create_wallet() {
    local role="$1"
    local default_name="$2"
    read -p "Enter wallet name for $role (default: $default_name): " wallet_name
    wallet_name=${wallet_name:-$default_name}

    echo -e "\n[INFO] Creating coldkey for $role ($wallet_name)..."
    btcli wallet new_coldkey --wallet.name "$wallet_name"
    echo -e "\n[INFO] Creating hotkey for $role ($wallet_name, hotkey: default)..."
    btcli wallet new_hotkey --wallet.name "$wallet_name" --wallet.hotkey default
    echo -e "\n[IMPORTANT] Please store your mnemonics for $role ($wallet_name) in a secure place!"
}

echo "[Wallet Setup] You will now create wallets for the following roles:"
echo "  1. Owner (creates and controls the subnet)"
echo "  2. Miner (registers as a subnet miner)"
echo "  3. Validator (registers as a subnet validator)"
echo "You can skip any wallet if you have already created it."
echo

# Owner wallet
read -p "Do you want to create the owner wallet? (y/n) [y]: " create_owner
create_owner=${create_owner:-y}
if [[ "$create_owner" =~ ^[Yy]$ ]]; then
    create_wallet "owner" "owner"
else
    echo "[INFO] Skipping owner wallet creation."
fi

echo
# Miner wallet
read -p "Do you want to create the miner wallet? (y/n) [y]: " create_miner
create_miner=${create_miner:-y}
if [[ "$create_miner" =~ ^[Yy]$ ]]; then
    create_wallet "miner" "miner"
else
    echo "[INFO] Skipping miner wallet creation."
fi

echo
# Validator wallet
read -p "Do you want to create the validator wallet? (y/n) [y]: " create_validator
create_validator=${create_validator:-y}
if [[ "$create_validator" =~ ^[Yy]$ ]]; then
    create_wallet "validator" "validator"
else
    echo "[INFO] Skipping validator wallet creation."
fi

echo
# List all wallets for confirmation
btcli wallet list

echo "[INFO] Wallet setup complete."
