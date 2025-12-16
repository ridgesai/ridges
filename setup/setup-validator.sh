#!/bin/bash

set -eux -o pipefail

sudo apt update -y


### PM2

sudo apt install -y npm
sudo npm install -g pm2


### UV

curl -Ls https://astral.sh/uv/install.sh | bash
export PATH="/home/ubuntu/.local/bin:$PATH"
echo 'export PATH="/home/ubuntu/.local/bin:$PATH"' >> /home/ubuntu/.bashrc


### DOCKER

# Add Docker's official GPG key:
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repository to Apt sources:
sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update -y

sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo groupadd -f docker
sudo usermod -aG docker "$USER"


### RIDGES VALIDATOR

git clone https://github.com/ridgesai/ridges.git ~/ridges
cd ~/ridges

uv venv --python 3.14
source .venv/bin/activate
uv pip install .

pm2 start 'uv run -m validator.main' --name ridges-validator
pm2 stop ridges-validator

exec sudo --user "$USER" bash -c "cd ~/ridges && source .venv/bin/activate && exec bash -l"
