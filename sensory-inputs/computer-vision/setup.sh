#!/usr/bin/env bash
set -e

echo "==================================================="
echo "[SETUP] Setting up Computer Vision Module (macOS)"
echo "==================================================="

# Check for python3 availability
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 not found! Please install Python 3.10 or 3.11 using Homebrew:"
    echo "        brew install python@3.11"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[INFO] Using Python version: $PYTHON_VERSION"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "[INFO] Creating virtual environment..."
    python3 -m venv venv
else
    echo "[INFO] Virtual environment already exists."
fi

# Activate virtual environment
echo "[INFO] Activating virtual environment..."
source venv/bin/activate

# Upgrade pip and install requirements
echo "[INFO] Upgrading pip and installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create default .env file if missing
if [ ! -f ".env" ]; then
    echo "[INFO] Creating default .env file..."
    cat << 'EOF' > .env
MQTT_HOST="hri.thecraceatree.com"
MQTT_PORT=443
MQTT_TOPIC="telemetry/cv"
CAMERA_INDEX=0
EOF
fi

echo "==================================================="
echo "[SUCCESS] Setup complete!"
echo "To run the CV module:"
echo "    source venv/bin/activate"
echo "    python vision_affect.py"
echo "==================================================="