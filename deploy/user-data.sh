#!/bin/bash
# EC2 "User data" script for Ubuntu Server 24.04 LTS.
# Paste this whole file into: Launch instance -> Advanced details -> User data.
# It runs once, as root, on the first boot (takes about 2-3 minutes).
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 docker-buildx unzip git
systemctl enable --now docker

# Let the default "ubuntu" user run docker without sudo (takes effect at the next login).
usermod -aG docker ubuntu

# 1 GB of swap so building the image on a 1 GB t3.micro never runs out of memory.
if [ ! -f /swapfile ]; then
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "Docker is ready" > /home/ubuntu/READY.txt
