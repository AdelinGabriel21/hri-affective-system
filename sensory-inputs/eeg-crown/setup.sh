#!/usr/bin/env bash
set -e

echo "=== [SETUP] Setting up EEG Crown virtual environment (macOS/Linux) ==="

# 1. Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# 2. Activate environment and install dependencies
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=== [SUCCESS] Setup complete! To run: source venv/bin/activate && python eeg_reader.py ==="