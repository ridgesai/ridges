# Main Sandbox Docker Image
# 
# This Dockerfile builds the container image used for running agent code.
# It provides a complete Python and JavaScript execution environment with
# all necessary tools for code manipulation and testing.
#
# Security Properties:
# - Based on slim Python image for minimal attack surface
# - No SSH daemon or unnecessary services running
# - Runs as root (required for file operations in mounted volumes)
# - No default CMD - container expects command at runtime
#
# Included Tools:
# - git: For repository operations
# - curl: For HTTP requests (via proxy)
# - patch/diffutils: For applying and creating patches
# - nvm: Node version manager for JavaScript support
#
# Mounted at Runtime:
# - /sandbox: Temporary directory with agent code, input.json, output.json

FROM python:3.14-slim

# Install system dependencies
# --no-install-recommends keeps image size minimal
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl patch diffutils && \
    rm -rf /var/lib/apt/lists/*



# ============================================
# Python Environment
# ============================================

COPY packages_py.txt /tmp/packages_py.txt

# Install Python packages from requirements file
# --no-cache-dir prevents pip from caching packages (reduces image size)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/packages_py.txt && \
    rm /tmp/packages_py.txt

# Clean up pip cache to minimize image size
RUN pip cache purge && \
    rm -rf /root/.cache/pip



# ============================================
# JavaScript/Node.js Environment
# ============================================

COPY packages_js.txt /tmp/packages_js.txt

# nvm (Node Version Manager) configuration
ENV NVM_DIR=/root/.nvm

# Install Node.js via nvm and global packages
# This provides a complete JavaScript runtime for JS agents
RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash && \
    . "$NVM_DIR/nvm.sh" && \
    nvm install --lts && \
    xargs -a /tmp/packages_js.txt npm install -g && \
    rm /tmp/packages_js.txt && \
    NODE_VERSION=$(nvm version default) && \
    ln -sf "$NVM_DIR/versions/node/$NODE_VERSION/bin/npm" /usr/local/bin/npm && \
    ln -sf "$NVM_DIR/versions/node/$NODE_VERSION/bin/npx" /usr/local/bin/npx && \
    ln -sf "$NVM_DIR/versions/node/$NODE_VERSION/bin/node" /usr/local/bin/node && \
    ln -sf "$NVM_DIR/versions/node/$NODE_VERSION/lib/node_modules" /usr/local/lib/node_modules

# Make global packages available
ENV NODE_PATH=/usr/local/lib/node_modules



# Working directory where /sandbox volume is mounted at runtime
WORKDIR /sandbox