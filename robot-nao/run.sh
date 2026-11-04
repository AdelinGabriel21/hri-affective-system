#!/usr/bin/env bash
# Run the NAO bridge or one of the tests with the project's venv.
#   bash run.sh          start the bridge
#   bash run.sh test     only check the connection to the robot (test.py)
#   bash run.sh send     send a test command over MQTT (test_send_command.py)
# On Windows, run.bat runs this inside WSL for you.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x venv/bin/python ]; then
    echo "[ERROR] No environment yet: run setup.bat (Windows) or 'bash setup.sh' (WSL) first."
    exit 1
fi

env_value() {  # value of KEY in .env, without quotes
    grep -E "^$1=" .env 2>/dev/null | head -n 1 | cut -d= -f2- | tr -d '"'"'"' \r'
}

# Choregraphe starts its virtual robot on a random port. For a local robot with no port
# set in .env, ask Windows which port naoqi-bin.exe is listening on.
NAO_IP_CONF=$(env_value NAO_IP)
if [ -z "$(env_value NAO_PORT)" ] && [[ "${NAO_IP_CONF:-127.0.0.1}" =~ ^(127\.0\.0\.1|localhost)$ ]] \
        && grep -qi microsoft /proc/version; then
    PORT=$(powershell.exe -NoProfile -Command '$p = Get-Process naoqi-bin -ErrorAction SilentlyContinue | Select-Object -First 1; if ($p) { (Get-NetTCPConnection -State Listen -OwningProcess $p.Id -ErrorAction SilentlyContinue | Select-Object -First 1).LocalPort }' | tr -d '\r')
    if [ -n "$PORT" ]; then
        export NAO_PORT="$PORT"
        echo "[RUN] Choregraphe virtual robot found on port $NAO_PORT"
    else
        echo "[RUN] Choregraphe's virtual robot is not running. Open Choregraphe first."
        exit 1
    fi
fi

case "${1:-bridge}" in
    bridge) exec venv/bin/python nao_mqtt_bridge.py ;;
    test)   exec venv/bin/python test.py ;;
    send)   exec venv/bin/python test_send_command.py ;;
    *)      echo "Usage: run.sh [bridge|test|send]"; exit 2 ;;
esac
