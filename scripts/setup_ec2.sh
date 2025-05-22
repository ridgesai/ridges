#!/usr/bin/env bash
set -euxo pipefail

####################     Usage    ###########################
# Run from local repo root:

# export ec2_host=<user@hostname>
# scp ./scripts/setup_ec2.sh $ec2_host:.
# ssh $ec2_host
# chmod +x setup_ec2.sh && ./setup_ec2.sh

#############################################################

# Update and upgrade the system
sudo apt-get -y update && sudo apt-get -y upgrade
# Install prerequisites for Python and Docker
sudo apt-get -y install software-properties-common apt-transport-https ca-certificates curl

# Python
#################################################################################
# Add Python 3.10 repository and install
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get -y update && sudo apt-get -y install python3.10 python3.10-venv python3.10-dev python3-pip

# Verify Python installation
python3.10 --version
echo "Python 3.10 has been installed successfully!"
################################################################################

# Docker
#########################################################################
# Add Docker's official GPG key
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# Add Docker repository
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Update package index and install Docker
sudo apt-get -y update
sudo apt-get -y install docker-ce docker-ce-cli containerd.io

# Enable and start Docker service
sudo systemctl enable --now docker

echo "Docker has been installed successfully!"
#########################################################################

# Project-specific things
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip

git clone --recurse-submodules https://github.com/taoagents/ridges
pushd ridges
pip install -e SWE-agent -e .
popd

# Enable running docker without sudo
sudo usermod -aG docker $USER
newgrp docker  # Starts new shell
