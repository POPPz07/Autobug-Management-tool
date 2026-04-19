#!/bin/bash
# =============================================================
# AutoRepro — EC2 Setup Script
# Run this once on a fresh Ubuntu 24.04 EC2 instance.
# Usage: bash setup.sh
# =============================================================

set -e  # stop on any error

echo ""
echo "======================================"
echo "  AutoRepro EC2 Setup — Starting..."
echo "======================================"
echo ""

# --------------------------------------------------------------
# 1. System update
# --------------------------------------------------------------
echo "[1/7] Updating system packages..."
sudo apt-get update -y && sudo apt-get upgrade -y
sudo apt-get install -y curl git unzip jq

# --------------------------------------------------------------
# 2. Install Docker
# --------------------------------------------------------------
echo "[2/7] Installing Docker..."
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

echo "[2/7] Installing Docker Compose plugin..."
sudo apt-get install -y docker-compose-plugin

# Activate docker group without requiring logout
echo "[2/7] Activating docker group for current session..."
newgrp docker << 'DOCKERGROUP'

# --------------------------------------------------------------
# 3. Clone the repository
# --------------------------------------------------------------
echo "[3/7] Cloning AutoRepro repository..."
REPO_URL="https://github.com/Inward17/Bug_Reproducer.git"

if [ -d "Bug_Reproducer" ]; then
  echo "  → Directory 'Bug_Reproducer' already exists, pulling latest..."
  cd Bug_Reproducer && git pull && cd ..
else
  git clone $REPO_URL
fi

cd Bug_Reproducer

# --------------------------------------------------------------
# 4. Create .env.production from template
# --------------------------------------------------------------
echo "[4/7] Setting up environment variables..."

if [ ! -f ".env.production" ]; then
  cp .env.production.template .env.production
  echo ""
  echo "  ⚠️  ACTION REQUIRED:"
  echo "  Open .env.production and fill in your API key:"
  echo "  $ nano .env.production"
  echo ""
  echo "  Once done, press ENTER to continue..."
  read -r
else
  echo "  → .env.production already exists, skipping."
fi

# --------------------------------------------------------------
# 5. Create data directories
# --------------------------------------------------------------
echo "[5/7] Creating data directories..."
mkdir -p data/jobs data/artifacts
chmod 755 data

# --------------------------------------------------------------
# 6. Build Docker images
# --------------------------------------------------------------
echo "[6/7] Building Docker images (this may take 5-10 minutes)..."
docker compose --profile build-only -f docker-compose.prod.yml build

echo "[6/7] Pre-building sandbox image..."
docker build -t autorepro-sandbox:latest ./autorepro/sandbox/

# --------------------------------------------------------------
# 7. Start the application
# --------------------------------------------------------------
echo "[7/7] Starting AutoRepro..."
docker compose -f docker-compose.prod.yml up -d

echo ""
echo "======================================"
echo "  ✅ AutoRepro is running!"
echo "======================================"
echo ""
echo "  Local URL:    http://localhost:8000
  Public URL:   http://ec2-3-239-28-71.compute-1.amazonaws.com:8000"
echo ""
echo "  To get your public URL, run:"
echo "  $ curl -s http://169.254.169.254/latest/meta-data/public-hostname"
echo ""
echo "  Useful commands:"
echo "  - View logs:    docker compose -f docker-compose.prod.yml logs -f"
echo "  - Stop app:     docker compose -f docker-compose.prod.yml down"
echo "  - Restart app:  docker compose -f docker-compose.prod.yml restart"
echo ""

DOCKERGROUP
