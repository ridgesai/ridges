# Use Ubuntu 22.04 as the base image
FROM ubuntu:22.04

# Set non-interactive mode for apt
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies (including libssl-dev, clang, and libclang-dev for Rust/OpenSSL)
RUN apt-get update && apt-get install -y \
    git curl make openssl libssl-dev llvm llvm-dev clang libclang-dev libclang-12-dev protobuf-compiler libusb-1.0-0-dev jq \
    python3.11 python3.11-venv python3-pip \
    build-essential pkg-config ncurses-dev \
    lsof netcat \
    libsoup2.4-dev \
    libjavascriptcoregtk-4.0-dev \
    libgtk-3-dev \
    libwebkit2gtk-4.0-dev \
    && rm -rf /var/lib/apt/lists/*

# Set python3.11 as default
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Clone and build Subtensor
RUN git clone https://github.com/opentensor/subtensor.git /subtensor
WORKDIR /subtensor
RUN ./scripts/init.sh && cargo build -p node-subtensor --profile release && \
    mkdir -p /subtensor/target/non-fast-blocks && \
    cp -r /subtensor/target/release /subtensor/target/non-fast-blocks/ && \
    mkdir -p /subtensor/target/fast-blocks && \
    cp -r /subtensor/target/release /subtensor/target/fast-blocks/

# Set up app directory
WORKDIR /app
COPY . /app

# Set up Python venv and install dependencies
RUN python3.11 -m venv /app/venv && \
    /app/venv/bin/pip install --upgrade pip && \
    if [ -d /app/deps/btcli ]; then /app/venv/bin/pip install -e /app/deps/btcli; fi && \
    if [ -d /app/deps/bittensor ]; then /app/venv/bin/pip install -e /app/deps/bittensor; fi && \
    if [ -d /app/SWE-agent ]; then /app/venv/bin/pip install -e /app/SWE-agent; fi && \
    /app/venv/bin/pip install -e /app

ENV PATH="/app/venv/bin:${PATH}"

# Default command (overridden by docker-compose)
CMD ["/bin/bash"] 