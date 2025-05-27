#!/bin/bash
set -e
# Starts the local Subtensor chain in the foreground.
# Reference: https://docs.bittensor.com/local-build/deploy

SUBTENSOR_DIR="subtensor"
CHAIN_PORT=9945

cd "$SUBTENSOR_DIR"

echo "[INFO] Starting Subtensor localnet (foreground)..."
# Note: To run in the background, you could use '&' or nohup, or run in a tmux/screen session.
# For now, we run in the foreground for easier debugging.
BUILD_BINARY=0 ./scripts/localnet.sh False &
SUBTENSOR_PID=$!

# Wait for the chain to start (check port 9945)
TIMEOUT=60
ELAPSED=0
SLEEP=2
while ! nc -z localhost $CHAIN_PORT; do
    sleep $SLEEP
    ELAPSED=$((ELAPSED+SLEEP))
    if [ $ELAPSED -ge $TIMEOUT ]; then
        echo "[ERROR] Subtensor chain did not start on port $CHAIN_PORT within $TIMEOUT seconds."
        kill $SUBTENSOR_PID 2>/dev/null || true
        exit 1
    fi
    echo "[INFO] Waiting for Subtensor chain to start on port $CHAIN_PORT... ($ELAPSED/$TIMEOUT s)"
done

echo "[INFO] Subtensor chain is running on ws://localhost:$CHAIN_PORT"
wait $SUBTENSOR_PID 

# If subtensor doesn't run, try restarting your whole computer. 