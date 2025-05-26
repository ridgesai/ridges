#!/bin/bash
set -e
# Orchestrates all setup scripts in the correct order for local Bittensor/Ridges development.

# 1. Install system dependencies
./scripts/install-dependencies.sh

# 2. Setup BTCLI and Bittensor SDK
./scripts/setup-btcli.sh

# 3. Setup Subtensor (clone, init, build)
./scripts/setup-subtensor.sh

# 4. Start Subtensor chain (foreground, user should keep this running in a separate terminal)
echo "[ACTION REQUIRED] Subtensor chain will now start in the foreground. Please keep this terminal open."
echo "[INFO] You may want to open a new terminal to continue the setup while the chain is running."
./scripts/start-subtensor.sh

# 5. Setup wallets (owner, miner, validator)
./scripts/setup-wallets.sh

# 6. Configure partial staking and fund wallets
./scripts/fund-wallets.sh

# 7. Register subnet and add stake
./scripts/register-subnet.sh

# 8. Register miner and validator on the subnet
./scripts/register-miner-validator.sh

# 9. Setup ridges and SWE-agent
./scripts/setup-ridges.sh

# 10. Prompt user to run miner and validator in new terminals
cat <<EOM

[ACTION REQUIRED]
To run the miner and validator, open new terminals and run:
  ./scripts/run-miner.sh
  ./scripts/run-validator.sh

[IMPORTANT]
If you restart the subtensor chain, you must re-fund wallets, re-register the subnet, and re-register the miner and validator on the subnet.

Follow the prompts in each script to provide wallet names, hotkeys, and netuid as needed.
EOM

echo "[INFO] All setup steps complete." 