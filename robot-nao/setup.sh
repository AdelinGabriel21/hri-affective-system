#!/usr/bin/env bash
# One-time setup for the NAO bridge. Run it in WSL (Ubuntu) or Linux:  bash setup.sh
# On Windows, setup.bat runs this inside WSL for you.
# The NAOqi Python SDK (qi) exists only for Linux/macOS with Python 3.7-3.12, not for Windows.
set -euo pipefail
cd "$(dirname "$0")"

echo "==================================================="
echo "[SETUP] NAO bridge environment"
echo "==================================================="

# 1. Python version supported by qi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
case "$PY_VERSION" in
    3.7|3.8|3.9|3.10|3.11|3.12) echo "[OK] Python $PY_VERSION" ;;
    *) echo "[ERROR] qi needs Python 3.7-3.12, this system has $PY_VERSION."; exit 1 ;;
esac

# 2. venv support is a separate package on Ubuntu/Debian
if command -v dpkg >/dev/null && ! dpkg -s python3-venv >/dev/null 2>&1; then
    echo "[SETUP] Installing python3-venv (asks for your Ubuntu password once)..."
    sudo apt-get update -q
    sudo apt-get install -y -q python3-venv
fi

# 3. Virtual environment and packages
if [ ! -x venv/bin/python ]; then
    echo "[SETUP] Creating virtual environment..."
    rm -rf venv
    python3 -m venv venv
fi
venv/bin/python -m pip install -q --upgrade pip
venv/bin/python -m pip install -q -r requirements.txt
echo "[OK] Packages installed: $(venv/bin/python -c 'import qi, paho.mqtt, dotenv; print("qi, paho-mqtt, python-dotenv")')"

# 4. Configuration
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[SETUP] Created .env from .env.example"
fi

# 5. WSL only: Choregraphe's virtual robot listens on Windows' 127.0.0.1, which WSL can
#    reach only in mirrored networking mode
NEEDS_RESTART=0
if grep -qi microsoft /proc/version; then
    MODE=$(wslinfo --networking-mode 2>/dev/null || echo unknown)
    if [ "$MODE" = "mirrored" ]; then
        echo "[OK] WSL networking: mirrored"
    else
        WIN_HOME=$(wslpath "$(powershell.exe -NoProfile -Command 'Write-Output $env:USERPROFILE' | tr -d '\r')")
        CONFIG="$WIN_HOME/.wslconfig"
        if [ ! -f "$CONFIG" ]; then
            printf '[wsl2]\nnetworkingMode=mirrored\n' > "$CONFIG"
            echo "[SETUP] Created $CONFIG with networkingMode=mirrored"
            NEEDS_RESTART=1
        elif grep -qiE '^\s*networkingMode\s*=\s*mirrored' "$CONFIG"; then
            NEEDS_RESTART=1
        else
            echo "[WARN] $CONFIG exists but does not set mirrored networking."
            echo "       Add these lines to it, then run 'wsl --shutdown' in Windows:"
            echo "         [wsl2]"
            echo "         networkingMode=mirrored"
        fi
    fi
fi

echo "==================================================="
if [ "$NEEDS_RESTART" = 1 ]; then
    echo "[SETUP] WSL must restart once to switch to mirrored networking."
    echo "        setup.bat does it for you; from a WSL terminal, run 'wsl.exe --shutdown' instead."
    echo "==================================================="
    exit 10
fi
echo "[SUCCESS] Setup complete. Open Choregraphe, then:"
echo "          run.bat (Windows) or 'bash run.sh' (WSL)       start the bridge"
echo "          run.bat test  / bash run.sh test               only test the robot connection"
echo "          run.bat send  / bash run.sh send               send a test command over MQTT"
echo "==================================================="
