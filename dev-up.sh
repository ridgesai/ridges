#!/usr/bin/env bash
set -e

###############################################################################
# dev-up.sh
# ----------
# • Builds the shared image (no-op if already cached)
# • Starts subtensor, miner, validator containers in detached mode
# • Runs wallet + subnet bootstrap ONCE, then skips on future runs
###############################################################################

# 1) Build (or reuse) the ridges-base image
echo "🔨  Building ridges-base image (skip if cached)…"
docker compose build

# 2) Start the containers
echo "🚀  Starting subtensor, miner, validator…"
docker compose up -d subtensor

# 3) One-time bootstrap: wallets, faucet funding, subnet/miner/validator registration
# BOOTSTRAP_FLAG=/subtensor/data/.chain_bootstrapped
# if ! docker compose exec subtensor test -f "$BOOTSTRAP_FLAG" ; then
#   echo "🪄  First-time chain detected – boot-strapping wallets & subnet."
#   docker compose exec -it subtensor bash /app/scripts/setup-wallets.sh
#   docker compose exec -it subtensor bash /app/scripts/fund-wallets.sh
#   docker compose exec -it subtensor bash /app/scripts/register-subnet.sh
#   docker compose exec -it subtensor bash /app/scripts/register-miner-validator.sh
#   # mark as done so we don't prompt next time
#   docker compose exec subtensor bash -c "touch $BOOTSTRAP_FLAG"
# else
#   echo "✅  Chain already boot-strapped – skipping wallet & subnet setup."
# fi
docker compose exec -it subtensor bash /app/scripts/setup-wallets.sh
docker compose exec -it subtensor bash /app/scripts/fund-wallets.sh
docker compose exec -it subtensor bash /app/scripts/register-subnet.sh
docker compose exec -it subtensor bash /app/scripts/register-miner-validator.sh
echo
echo "🎉  Local net is up! RPC endpoint →  ws://localhost:9945"
echo "    Tail logs with: docker compose logs -f subtensor"

