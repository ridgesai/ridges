#!/bin/bash
set -e
# Installs system-level dependencies (brew, Python, Rust, Docker, and other tools) for macOS

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check for Homebrew
if ! command -v brew &> /dev/null; then
    echo -e "${YELLOW}Homebrew not found. Installing Homebrew...${NC}"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
    echo -e "${GREEN}Homebrew already installed.${NC}"
fi

echo -e "${GREEN}Updating Homebrew...${NC}"
brew update

# Install dependencies
BREW_DEPS=(make git curl openssl llvm protobuf libusb jq docker)
for dep in "${BREW_DEPS[@]}"; do
    if brew list "$dep" &>/dev/null; then
        echo -e "${GREEN}$dep already installed.${NC}"
    else
        echo -e "${YELLOW}Installing $dep...${NC}"
        brew install "$dep"
    fi
done

# Check for Python 3.10 or 3.11
PYTHON_BIN=""
if command -v python3.11 &> /dev/null; then
    PYTHON_BIN="python3.11"
    echo -e "${GREEN}Python 3.11 found.${NC}"
elif command -v python3.10 &> /dev/null; then
    PYTHON_BIN="python3.10"
    echo -e "${GREEN}Python 3.10 found.${NC}"
else
    echo -e "${YELLOW}Python 3.10/3.11 not found. Installing Python 3.11...${NC}"
    brew install python@3.11
    PYTHON_BIN="python3.11"
fi

# Ensure pip is up to date
$PYTHON_BIN -m pip install --upgrade pip

# Install Rust (if not present)
if ! command -v rustc &> /dev/null; then
    echo -e "${YELLOW}Rust not found. Installing Rust...${NC}"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
else
    echo -e "${GREEN}Rust already installed.${NC}"
fi

# Ensure Rust nightly and wasm target for Subtensor
if ! rustup toolchain list | grep -q nightly; then
    echo -e "${YELLOW}Installing Rust nightly toolchain...${NC}"
    rustup toolchain install nightly
fi
if ! rustup target list --installed | grep -q wasm32-unknown-unknown; then
    echo -e "${YELLOW}Adding wasm32-unknown-unknown target...${NC}"
    rustup target add wasm32-unknown-unknown --toolchain nightly
fi

echo -e "${GREEN}All dependencies installed.${NC}" 