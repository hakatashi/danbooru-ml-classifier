#!/usr/bin/env bash
# Install Qdrant vector database as a systemd system service using Docker.
# Qdrant stores data in /var/lib/qdrant-danbooru (SSD, not /mnt/cache).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/qdrant.service"
SYSTEM_UNIT_DIR="/etc/systemd/system"
QDRANT_DATA_DIR="/var/lib/qdrant-danbooru"

# ── 1. Pull the Qdrant Docker image ───────────────────────────────────────────
echo "Pulling Qdrant Docker image ..."
docker pull qdrant/qdrant

# ── 2. Create data directory ───────────────────────────────────────────────────
echo "Creating data directory $QDRANT_DATA_DIR ..."
sudo mkdir -p "$QDRANT_DATA_DIR"

# ── 3. Install systemd system service ─────────────────────────────────────────
echo "Installing $SYSTEM_UNIT_DIR/qdrant.service ..."
sudo cp "$SERVICE_FILE" "$SYSTEM_UNIT_DIR/qdrant.service"

sudo systemctl daemon-reload
sudo systemctl enable --now qdrant.service
echo "Service status:"
sudo systemctl status qdrant.service --no-pager || true

# ── 4. Install Python client ───────────────────────────────────────────────────
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "Installing qdrant-client into worker venv ..."
"$REPO_DIR/worker/venv/bin/pip" install -q "qdrant-client>=1.9.0"

echo ""
echo "Done! Qdrant is running at http://127.0.0.1:6333"
echo "Dashboard: http://127.0.0.1:6333/dashboard"
