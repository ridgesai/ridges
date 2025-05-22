#!/usr/bin/env bash
set -euxo pipefail

function start-localnet() {
    pm2 start -f "${SUBTENSOR_ROOT}/scripts/localnet.sh" --name localnet -- False --no-purge
    pm2 start "${RIDGES_ROOT}/scripts/setup_staging_subnet.sh" --name localnet-setup --no-autorestart
}

start-localnet