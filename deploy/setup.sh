#!/usr/bin/env bash
# One-shot setup for a fresh Ubuntu 24.04 Vultr Cloud Compute VM.
set -euo pipefail
apt-get update -y
apt-get install -y docker.io python3-venv git curl
systemctl enable --now docker
# gVisor (runsc) as an extra Docker runtime
curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" > /etc/apt/sources.list.d/gvisor.list
apt-get update -y && apt-get install -y runsc
runsc install && systemctl restart docker
cd /opt/counterproof
docker build -t counterproof-runner sandbox/
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
cp deploy/counterproof.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now counterproof
