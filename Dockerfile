# syntax=docker/dockerfile:1   ← enables BuildKit cache mounts
############################################################
#  Stage 1 – builder / compiler
############################################################
FROM ubuntu:22.04 AS build

ARG DEBIAN_FRONTEND=noninteractive
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

# Python 3.11 default
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Rust tool-chain
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# ---- build subtensor ----
# Clone and build Subtensor
# Line 30 might not work if Cargo.toml file is changed.
RUN git clone https://github.com/opentensor/subtensor.git /subtensor \
 && sed -i 's|pow-faucet *= *\[\]|pow-faucet = ["node-subtensor-runtime/pow-faucet"]|' /subtensor/node/Cargo.toml
WORKDIR /subtensor
RUN ./scripts/init.sh && cargo build -p node-subtensor --profile release --features pow-faucet && \
    mkdir -p /subtensor/target/non-fast-blocks && \
    cp -r /subtensor/target/release /subtensor/target/non-fast-blocks/ && \
    mkdir -p /subtensor/target/fast-blocks && \
    cp -r /subtensor/target/release /subtensor/target/fast-blocks/

# ---- build Python env & your app ----
WORKDIR /app
COPY . /app

RUN python3.11 -m pip install --upgrade pip uv && \
    uv venv /app/venv --python=/usr/bin/python3.11 --seed && \
    /app/venv/bin/pip install uv && \
    . /app/venv/bin/activate && \
    uv pip install -e /app && \
    uv pip install bittensor-cli && \
    if [ -d /app/deps/btcli ]; then uv pip install -e /app/deps/btcli; fi && \
    if [ -d /app/deps/bittensor ]; then uv pip install -e /app/deps/bittensor; fi

ENV PATH="/app/venv/bin:${PATH}"

############################################################
#  Stage 2 – runtime
############################################################
FROM ubuntu:22.04 AS runtime

SHELL ["/bin/bash", "-c"]

# Install all major build/runtime dependencies for Python/Rust/C/C++/GTK/etc
RUN apt-get update && apt-get install -y \
    python3.11 python3.11-venv python3-pip \
    git curl make openssl libssl-dev llvm llvm-dev clang libclang-dev libclang-12-dev protobuf-compiler libusb-1.0-0-dev jq \
    build-essential pkg-config ncurses-dev \
    lsof netcat \
    libsoup2.4-dev \
    libjavascriptcoregtk-4.0-dev \
    libgtk-3-dev \
    libwebkit2gtk-4.0-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Rust toolchain
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Install maturin (needed for Rust-based Python packages)
RUN source $HOME/.cargo/env && python3.11 -m pip install --upgrade pip maturin

# Install bittensor-cli globally (requires Rust and maturin)
RUN source $HOME/.cargo/env && python3.11 -m pip install bittensor-cli

#   1) subtensor binary + auxiliary dirs
COPY --from=build /subtensor /subtensor
COPY --from=build /subtensor/target/release/node-subtensor /usr/local/bin/
COPY --from=build /subtensor/target/non-fast-blocks /subtensor/target/non-fast-blocks
COPY --from=build /subtensor/target/fast-blocks      /subtensor/target/fast-blocks

#   2) python venv + application
COPY --from=build /app/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
WORKDIR /app
COPY --from=build /app/. /app

## Port used by miner
EXPOSE 7999 

CMD ["/bin/bash"]    # overridden by docker-compose service commands
