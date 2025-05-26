#!/bin/bash
set -e
# Sets up BTCLI and Bittensor SDK in a shared Python virtual environment.
# References:
# https://docs.bittensor.com/getting-started/install-btcli
# https://docs.bittensor.com/getting-started/installation

# Check for Homebrew, Python, Rust, Docker (from install-dependencies.sh)
for dep in brew python3.11 rustc docker; do
    if ! command -v $dep &> /dev/null; then
        echo "[ERROR] Required dependency '$dep' not found. Please run install-dependencies.sh first."
        exit 1
    fi
done

# Set up directories
DEPS_DIR="deps"
BTCLI_REPO="https://github.com/opentensor/btcli.git"
BTENSOR_REPO="https://github.com/opentensor/bittensor.git"
BTCLI_DIR="$DEPS_DIR/btcli"
BTENSOR_DIR="$DEPS_DIR/bittensor"
VENV_DIR="$DEPS_DIR/.venv"
PYTHON_BIN="python3.11"

# Create deps directory if it doesn't exist
mkdir -p "$DEPS_DIR"

# Clone BTCLI if not present
if [ ! -d "$BTCLI_DIR" ]; then
    echo "Cloning BTCLI..."
    git clone "$BTCLI_REPO" "$BTCLI_DIR"
else
    echo "BTCLI already cloned."
fi

# Clone Bittensor SDK if not present
if [ ! -d "$BTENSOR_DIR" ]; then
    echo "Cloning Bittensor SDK..."
    git clone "$BTENSOR_REPO" "$BTENSOR_DIR"
else
    echo "Bittensor SDK already cloned."
fi

# Create venv if not present
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment..."
    $PYTHON_BIN -m venv "$VENV_DIR"
    if [ ! -d "$VENV_DIR" ]; then
        echo "[ERROR] Failed to create virtual environment at $VENV_DIR."
        exit 1
    fi
else
    echo "Virtual environment already exists."
fi

# Activate venv
source "$VENV_DIR/bin/activate"

# Upgrade pip
pip install --upgrade pip

# Install BTCLI in editable mode
cd "$BTCLI_DIR"
pip install -e .
cd -

# Install Bittensor SDK in editable mode
cd "$BTENSOR_DIR"
pip install -e .
cd -

echo "BTCLI and Bittensor SDK setup complete."
echo "To activate the environment in future scripts, use: source $VENV_DIR/bin/activate" 