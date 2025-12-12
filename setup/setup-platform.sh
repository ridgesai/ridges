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


### RIDGES PLATFORM

git clone https://github.com/ridgesai/ridges.git ~/ridges
cd ~/ridges

uv venv --python 3.11
source .venv/bin/activate
uv pip install .

pm2 start 'uv run -m api.main' --name ridges-platform
pm2 stop ridges-platform

exec sudo --user "$USER" bash -c "cd ~/ridges && source .venv/bin/activate && exec bash -l"