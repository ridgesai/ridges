#!/bin/bash
set -e
# Funds Bittensor wallets using the faucet and ensures partial staking is enabled.
# Reference: https://github.com/opentensor/bittensor-subnet-template/blob/main/docs/running_on_staging.md#5-initialize

# Path to shared venv (created by setup-btcli.sh)
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

# Prompt user to enable partial staking
echo "[ACTION REQUIRED] You must enable partial staking in your btcli config."
echo "This is required for staking and registration."
echo "The btcli config tool will now open. Please set 'allow_partial_stake' to true."
btcli config set

echo
# Prompt for wallet names (default: owner, miner, validator)
# There might be a better way to do this, cuz it could be stored previously when they were created, cuz what if the user somehow forgets in the timespan of a minute. 
read -p "Enter owner wallet name (default: owner): " owner_wallet
owner_wallet=${owner_wallet:-owner}
read -p "Enter miner wallet name (default: miner): " miner_wallet
miner_wallet=${miner_wallet:-miner}
read -p "Enter validator wallet name (default: validator): " validator_wallet
validator_wallet=${validator_wallet:-validator}

# Fund each wallet using the faucet
fund_wallet() {
    local wallet_name="$1"
    read -p "Do you want to fund wallet '$wallet_name' with the faucet? (y/n) [y]: " fund
    fund=${fund:-y}
    if [[ "$fund" =~ ^[Yy]$ ]]; then
        echo "[INFO] Funding wallet '$wallet_name'..."
        btcli wallet faucet --wallet.name "$wallet_name" --subtensor.chain_endpoint ws://127.0.0.1:9945 || {
            echo "[ERROR] Faucet funding failed for wallet '$wallet_name'.";
            return 1;
        }
        echo "[INFO] Wallet '$wallet_name' funded."
    else
        echo "[INFO] Skipping funding for wallet '$wallet_name'."
    fi
}

echo
fund_wallet "$owner_wallet"
echo
fund_wallet "$miner_wallet"
echo
fund_wallet "$validator_wallet"

echo "[INFO] Wallet funding complete." 