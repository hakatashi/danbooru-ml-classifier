#!/usr/bin/env bash
# Create and populate the Python virtual environment for the PU Learning project.
# Run from the pu-learning/ directory:
#   bash setup.sh

set -euo pipefail

ROCM_INDEX_URL="https://download.pytorch.org/whl/rocm6.2"

cd "$(dirname "$0")"

echo "=== Creating virtual environment ==="
python3 -m venv venv

echo "=== Upgrading pip ==="
venv/bin/pip install --upgrade pip

echo "=== Installing PyTorch with ROCm 6.2 ==="
venv/bin/pip install \
    torch==2.5.1 \
    torchvision==0.20.1 \
    --index-url "$ROCM_INDEX_URL"

echo "=== Installing remaining dependencies ==="
venv/bin/pip install -r requirements.txt

echo ""
echo "Done. Activate with:  source venv/bin/activate"
