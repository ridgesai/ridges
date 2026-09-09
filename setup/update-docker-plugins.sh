#!/usr/bin/env bash
# Temporary startup update.
set -euo pipefail

if [[ "$(uname -s)" != Linux ]]; then
    # Docker Desktop ships current plugins and maps file ownership itself.
    echo "Docker plugin update only applies to Linux hosts; skipping on $(uname -s)"
    exit 0
fi
case "$(uname -m)" in
    x86_64) compose_arch=x86_64; buildx_arch=amd64 ;;
    aarch64|arm64) compose_arch=aarch64; buildx_arch=arm64 ;;
    *) echo "Unsupported Docker plugin architecture: $(uname -m)" >&2; exit 1 ;;
esac

versions_match() {
    local compose_version buildx_version
    compose_version=$(docker compose version --short 2>/dev/null || true)
    buildx_version=$(docker buildx version 2>/dev/null | awk '{print $2}' || true)
    [[ "${compose_version#v}" == 5.5.1 && "${buildx_version#v}" == 0.37.0 ]]
}

if versions_match; then
    echo "Docker Compose 5.5.1 and Buildx 0.37.0 already installed"
    exit 0
fi

plugin_dir="${DOCKER_CONFIG:-$HOME/.docker}/cli-plugins"
mkdir -p "$plugin_dir"
plugin_dir=$(cd "$plugin_dir" && pwd)
export DOCKER_CONFIG="${plugin_dir%/cli-plugins}"
# Stage beside the destinations so replacement uses a same-filesystem rename.
plugin_tmp=$(mktemp -d "$plugin_dir/.ridges-update.XXXXXX")
trap 'rm -rf -- "$plugin_tmp"' EXIT
cd "$plugin_tmp"

compose_file="docker-compose-linux-$compose_arch"
buildx_file="buildx-v0.37.0.linux-$buildx_arch"
curl -fsSLO --connect-timeout 15 --max-time 120 --retry 2 "https://github.com/docker/compose/releases/download/v5.5.1/$compose_file"
curl -fsSLO --connect-timeout 15 --max-time 120 --retry 2 "https://github.com/docker/compose/releases/download/v5.5.1/$compose_file.sha256"
curl -fsSLO --connect-timeout 15 --max-time 120 --retry 2 "https://github.com/docker/buildx/releases/download/v0.37.0/$buildx_file"
curl -fsSLO --connect-timeout 15 --max-time 120 --retry 2 https://github.com/docker/buildx/releases/download/v0.37.0/checksums.txt
sha256sum -c "$compose_file.sha256"
sha256sum -c --ignore-missing checksums.txt

chmod 755 "$compose_file" "$buildx_file"
mv -f -- "$compose_file" ../docker-compose
mv -f -- "$buildx_file" ../docker-buildx
docker compose version
docker buildx version
if ! versions_match; then
    echo "Docker is not selecting the installed versions; check DOCKER_CONFIG and cliPluginsExtraDirs" >&2
    exit 1
fi
