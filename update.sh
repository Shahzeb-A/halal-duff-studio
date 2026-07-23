#!/bin/bash
# One command to pull all the latest changes + refresh dependencies (macOS / Linux).
cd "$(dirname "$0")" || exit 1
echo "Pulling latest from GitHub..."
git pull --ff-only origin main || { echo "  Pull failed (you may have local changes). Run: git status"; exit 1; }
if [ -x ".venv/bin/pip" ]; then
  echo "Refreshing dependencies..."
  .venv/bin/pip install -r requirements.txt -q
fi
echo "  Up to date. Launch with the icon / launcher for your OS."
