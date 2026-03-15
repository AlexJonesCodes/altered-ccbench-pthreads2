#!/usr/bin/env bash
# setup_wsl.sh — One-shot WSL environment setup for altered-ccbench-pthreads2
#
# Usage:  chmod +x setup_wsl.sh && ./setup_wsl.sh
#
# What this does:
#   1. Installs system packages (build-essential, cmake, libnuma-dev, etc.)
#   2. Creates a Python virtual environment with matplotlib + numpy
#   3. Builds ccbench
#
# NOTE: perf (linux-tools) is NOT available for the WSL Microsoft kernel.
#       ccbench itself does NOT require perf — it uses its own timing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "=== [1/4] Installing system packages ==="
sudo apt-get update -qq
sudo apt-get install -y \
  git \
  build-essential \
  cmake \
  pkg-config \
  curl \
  wget \
  unzip \
  htop \
  python3 \
  python3-pip \
  python3-venv \
  libnuma-dev \
  libpthread-stubs0-dev

echo ""
echo "=== [2/4] Creating Python virtual environment ==="
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
  echo "Created venv at $VENV_DIR"
else
  echo "Venv already exists at $VENV_DIR"
fi

# Install Python dependencies inside the venv
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install matplotlib numpy

echo ""
echo "=== [3/4] Building ccbench ==="
cd "$SCRIPT_DIR"
make clean 2>/dev/null || true
make

echo ""
echo "=== [4/4] Done! ==="
echo ""
echo "To run visualization scripts, activate the venv first:"
echo ""
echo "  source .venv/bin/activate"
echo ""
echo "Then run your script as usual:"
echo ""
echo "  python3 scripts/visualize_stickiness_timeline.py \\"
echo "      results/stickiness_study/analysis \\"
echo "      --out-dir results/stickiness_study/timeline_plots \\"
echo "      --format png --dpi 150"
echo ""
echo "NOTE: perf / linux-tools is NOT available on WSL's Microsoft kernel."
echo "      ccbench uses its own cycle-level timing and does not need perf."
