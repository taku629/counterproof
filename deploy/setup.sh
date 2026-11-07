#!/usr/bin/env bash
# One-shot setup for a fresh Ubuntu 24.04 Vultr Cloud Compute VM. Run as root from /opt/counterproof.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y docker.io python3-venv git curl gnupg
systemctl enable --now docker
RUNTIME=runsc
# gVisor (runsc) as an extra Docker runtime; fall back to runc if it cannot be installed
if ! command -v runsc >/dev/null; then
  ( curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor --yes -o /usr/share/keyrings/gvisor-archive-keyring.gpg &&
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" > /etc/apt/sources.list.d/gvisor.list &&
    apt-get update -y && apt-get install -y runsc && runsc install && systemctl restart docker ) || RUNTIME=""
fi
cd /opt/counterproof
docker build -t counterproof-runner sandbox/
if [ -n "$RUNTIME" ]; then
  docker run --rm --runtime runsc --network none --entrypoint true counterproof-runner || RUNTIME=""
fi
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
touch .env && chmod 600 .env
grep -q '^SANDBOX_RUNTIME=' .env || echo "SANDBOX_RUNTIME=$RUNTIME" >> .env
grep -q '^VULTR_MODEL=' .env || echo "VULTR_MODEL=${VULTR_MODEL:-qwen3.8-27b}" >> .env
grep -q '^SETUP_TOKEN=' .env || echo "SETUP_TOKEN=${SETUP_TOKEN:-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')}" >> .env
cp deploy/counterproof.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable counterproof && systemctl restart counterproof
echo "runtime=${RUNTIME:-runc}"
