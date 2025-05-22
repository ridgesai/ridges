#!/usr/bin/env bash
# Ridges Development Environment Bootstrap One-Liner
# This script downloads and executes the Ridges bootstrap script
# Usage: curl -sSL https://raw.githubusercontent.com/ridgesai/ridges/single-local-deploy/bootstrap-oneliner.sh | bash

# Download and execute the bootstrap script
curl -sSL https://raw.githubusercontent.com/ridgesai/ridges/single-local-deploy/bootstrap.sh -o /tmp/bootstrap.sh && chmod +x /tmp/bootstrap.sh && /tmp/bootstrap.sh 