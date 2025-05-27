#!/bin/bash
set -e
# Sets up the Subtensor blockchain for local development.
# Reference: https://docs.bittensor.com/local-build/deploy

SUBTENSOR_REPO="https://github.com/opentensor/subtensor.git"
SUBTENSOR_DIR="subtensor"

# Clone subtensor if not present
if [ ! -d "$SUBTENSOR_DIR" ]; then
    echo "[INFO] Cloning Subtensor repo..."
    git clone "$SUBTENSOR_REPO" "$SUBTENSOR_DIR"
else
    echo "[INFO] Subtensor repo already exists."
fi

cd "$SUBTENSOR_DIR"

# Run Rust toolchain setup
if [ -f "scripts/init.sh" ]; then
    echo "[INFO] Running Rust toolchain setup (scripts/init.sh)..."
    ./scripts/init.sh
else
    echo "[ERROR] scripts/init.sh not found!"
    exit 1
fi

# Build subtensor binary with faucet feature enabled
echo "[INFO] Building Subtensor binary (this may take a while)..."
cargo build -p node-subtensor --profile release --features pow-faucet || { echo "[ERROR] Cargo build failed."; exit 1; }

echo "[INFO] Subtensor setup complete."
cd - 