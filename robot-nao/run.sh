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
    # The Windows call occasionally returns nothing, so retry a few times
    PORT=""
    for _ in 1 2 3; do
        PORT=$(powershell.exe -NoProfile -Command '$p = Get-Process naoqi-bin -ErrorAction SilentlyContinue | Select-Object -First 1; if ($p) { (Get-NetTCPConnection -State Listen -OwningProcess $p.Id -ErrorAction SilentlyContinue | Select-Object -First 1).LocalPort }' < /dev/null | tr -d '\r')
        [ -n "$PORT" ] && break
        sleep 1
    done
    if [ -n "$PORT" ]; then
        export NAO_PORT="$PORT"
        echo "[RUN] Choregraphe virtual robot found on port $NAO_PORT"
    else
        echo "[RUN] Choregraphe's virtual robot is not running. Open Choregraphe first."
        exit 1
    fi

    # ALMotion, ALTextToSpeech etc. run in a second Choregraphe process that listens only on
    # 127.0.0.1, but the robot also advertises the laptop's other addresses, which hang from WSL.
    # qi picks 127.0.0.1 only if it thinks it runs on the robot's machine, so give WSL's qi the
    # virtual robot's machine id (the original is kept in machine_id.backup).
    ROBOT_ID=$(timeout 20 venv/bin/python - "$NAO_PORT" 2>/dev/null <<'EOF' || true
import sys, qi
s = qi.Session()
s.connect(f"tcp://127.0.0.1:{sys.argv[1]}")
print(next(i["machineId"] for i in s.services() if i["name"] == "ServiceDirectory"))
EOF
)
    # qi writes its own log lines to stdout too: keep only the UUID
    ROBOT_ID=$(printf '%s\n' "$ROBOT_ID" | grep -oE '[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}' | tail -n 1 || true)
    ID_FILE="$HOME/.config/qimessaging/machine_id"
    if [ -n "$ROBOT_ID" ] && [ "$(cat "$ID_FILE" 2>/dev/null)" != "$ROBOT_ID" ]; then
        mkdir -p "$(dirname "$ID_FILE")"
        if [ -f "$ID_FILE" ] && [ ! -f "$ID_FILE.backup" ]; then
            cp "$ID_FILE" "$ID_FILE.backup"
        fi
        printf '%s' "$ROBOT_ID" > "$ID_FILE"
        echo "[RUN] qi machine id set to the virtual robot's ($ROBOT_ID)"
    elif [ -z "$ROBOT_ID" ]; then
        echo "[WARN] Could not read the virtual robot's machine id; service calls may hang."
    fi
fi

case "${1:-bridge}" in
    bridge) exec venv/bin/python nao_mqtt_bridge.py ;;
    test)   exec venv/bin/python test.py ;;
    send)   exec venv/bin/python test_send_command.py ;;
    *)      echo "Usage: run.sh [bridge|test|send]"; exit 2 ;;
esac
