#!/usr/bin/env bash
# File: init_apex_wallets.sh
# Creates owner / miner / validator wallets, faucets τ, registers them
# on apex (netuid 1), and publishes the miner's endpoint via Fiber.

set -e

# ---- helper: run btcli inside the subtensor container ---------------
btc() {
  docker compose exec subtensor btcli "$@"
}

# ---- helper: run fiber-post-ip inside the miner container ----------
fiber() {
  docker compose exec miner fiber-post-ip "$@"
}

# Build the base image (profile "builder")
# If you've already built the base image, you can comment out this line, because running it will rebuild the image and take forever.

# DOCKER_BUILDKIT=1 docker compose --profile builder build ridges-base

# 1) be sure the chain is running
docker compose up -d subtensor
sleep 5   # short pause for first blocks

btc config set --subtensor.chain_endpoint ws://subtensor:9945

# 2) create wallets
btc wallet new_coldkey --wallet.name owner_ridges
btc wallet new_coldkey --wallet.name miner_ridges
btc wallet new_hotkey  --wallet.name miner_ridges     --wallet.hotkey default_ridges
btc wallet new_coldkey --wallet.name validator_ridges
btc wallet new_hotkey  --wallet.name validator_ridges --wallet.hotkey default_ridges

# 3) faucet tokens
btc wallet faucet --wallet.name owner_ridges
btc wallet faucet --wallet.name miner_ridges
btc wallet faucet --wallet.name validator_ridges

# 4) register miner & validator on apex
btc subnet register --wallet.name miner_ridges     --wallet.hotkey default_ridges --netuid 1
btc subnet register --wallet.name validator_ridges --wallet.hotkey default_ridges --netuid 1

docker compose up -d miner

# 5) publish miner's IP/port for the validator to find it
# EXTERNAL_IP=$(ipconfig getifaddr en0) ./ridges-localnet.sh 
# ^ for full network access, use your machine's external IP
# for testing you can just run ./ridges-localnet.sh
fiber \
  --netuid 1 \
  --subtensor.chain_endpoint ws://subtensor:9945 \
  --external_ip "${EXTERNAL_IP:-0.0.0.1}" \
  --external_port 7999 \
  --wallet.name miner_ridges \
  --wallet.hotkey default_ridges

echo "✅  Wallets funded, registered, and miner IP posted."
echo "   Now run:  docker compose up -d miner validator"
