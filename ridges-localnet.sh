#!/usr/bin/env bash
# File: init_apex_wallets.sh
# Creates owner / miner / validator wallets, faucets τ, and registers
# miner + validator on apex (netuid 1).

set -e

# ---- helper: run btcli inside the subtensor container ---------------
btc() {
  docker compose exec subtensor btcli "$@"
}
# Build the base image
DOCKER_BUILDKIT=1 docker compose --profile builder build ridges-base

# 1) be sure the chain is running
docker compose up -d subtensor
sleep 5   # short pause for first blocks

btc config set --subtensor.chain_endpoint ws://subtensor:9945
# 2) create wallets (Step 7)
btc wallet new_coldkey --wallet.name owner
btc wallet new_coldkey --wallet.name miner
btc wallet new_hotkey  --wallet.name miner     --wallet.hotkey default
btc wallet new_coldkey --wallet.name validator
btc wallet new_hotkey  --wallet.name validator --wallet.hotkey default

# 3) faucet tokens (Step 8)
btc wallet faucet --wallet.name owner
btc wallet faucet --wallet.name miner
btc wallet faucet --wallet.name validator

# 4) register miner & validator on apex (Step 10, netuid 1)
btc subnet register --wallet.name miner     --wallet.hotkey default --netuid 1
btc subnet register --wallet.name validator --wallet.hotkey default --netuid 1

echo "✅  Wallets funded and miner/validator registered on apex."
echo "   You can now run:  docker compose up -d miner validator"
