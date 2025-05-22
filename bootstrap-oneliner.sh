#!/usr/bin/env bash
# Ridges Development Environment Bootstrap One-Liner
# Usage: curl -sSL https://raw.githubusercontent.com/taoagents/ridges/main/bootstrap-oneliner.sh | bash

# Generate the bootstrap command
cat << 'EOL'
curl -sSL https://raw.githubusercontent.com/taoagents/ridges/main/bootstrap.sh -o /tmp/bootstrap.sh && chmod +x /tmp/bootstrap.sh && /tmp/bootstrap.sh
EOL

# Example of how to use this script:
cat << 'EOL'

# To bootstrap your Ridges development environment with a single command, run:
curl -sSL https://raw.githubusercontent.com/taoagents/ridges/main/bootstrap-oneliner.sh | bash
EOL 