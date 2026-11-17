#!/usr/bin/env bash
# Install the fusion engine as a systemd service on the hub server (the LXC that runs Mosquitto),
# so it starts with the server and restarts by itself after a crash.
#   sudo bash install_service.sh
# Run it again after `git pull` to install new dependencies and restart with the new code.
#
#   status:   systemctl status hri-fusion-engine
#   logs:     journalctl -u hri-fusion-engine -f
#   restart:  sudo systemctl restart hri-fusion-engine   (also resets the baseline)
#   remove:   sudo systemctl disable --now hri-fusion-engine
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
SERVICE=hri-fusion-engine

if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] Run it with sudo: sudo bash install_service.sh"
    exit 1
fi
case "$DIR" in
    *" "*) echo "[ERROR] systemd needs a path without spaces; clone the repo elsewhere (e.g. /opt)."; exit 1 ;;
esac
# The service runs as whoever called sudo (root when logged in as root, common in an LXC)
RUN_USER="${SUDO_USER:-root}"

echo "==================================================="
echo "[SETUP] Fusion engine service ($SERVICE)"
echo "==================================================="

# 1. Python virtual environment (venv support is a separate package on Debian/Ubuntu)
if command -v dpkg >/dev/null && ! dpkg -s python3-venv >/dev/null 2>&1; then
    apt-get update -q
    apt-get install -y -q python3-venv
fi
if [ ! -x venv/bin/python ]; then
    sudo -u "$RUN_USER" python3 -m venv venv
fi
sudo -u "$RUN_USER" venv/bin/python -m pip install -q --upgrade pip
sudo -u "$RUN_USER" venv/bin/python -m pip install -q -r requirements.txt

# 2. Configuration: the defaults (localhost:1883, no TLS) are right when Mosquitto runs here
if [ ! -f .env ]; then
    sudo -u "$RUN_USER" cp .env.example .env
    echo "[SETUP] Created .env from .env.example (broker on localhost:1883)"
fi

# 3. systemd unit. PYTHONUNBUFFERED makes the prints show up in journalctl right away.
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=HRI fusion engine (telemetry/# -> affect/state, robot/command)
After=network-online.target mosquitto.service
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$DIR/venv/bin/python $DIR/fusion_engine.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
systemctl restart "$SERVICE"
sleep 3

echo "==================================================="
if systemctl is-active --quiet "$SERVICE"; then
    echo "[SUCCESS] $SERVICE is running. Last log lines:"
    journalctl -u "$SERVICE" -n 5 --no-pager -o cat
    echo "Follow the logs with: journalctl -u $SERVICE -f"
else
    echo "[ERROR] $SERVICE did not start. Last log lines:"
    journalctl -u "$SERVICE" -n 20 --no-pager -o cat
    exit 1
fi
echo "==================================================="
