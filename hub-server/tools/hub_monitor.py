"""Live view of the hub for demos and debugging (read-only).

Once per second it prints what each sensor sends and what the fusion engine concludes,
plus session phases, commands to the robot and the robot's status:

    python hub_monitor.py

Broker settings come from .env (see .env.example); the default is the public broker.
"""
import json
import os
import time

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "hri.thecraceatree.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", 443))
USE_WSS = os.getenv("MQTT_USE_WSS", "true").lower() == "true"
MQTT_USERNAME = os.getenv("MQTT_USERNAME") or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD") or None

latest = {"polar": None, "eeg": None, "cv": None}  # last "ok" data per sensor, None otherwise
start = time.time()


def fmt(value, spec="+.2f"):
    return "  --  " if value is None else format(value, spec)


def sensor_summary():
    p, e, c = latest["polar"], latest["eeg"], latest["cv"]
    polar = f"HR {p['hr_bpm']:5.1f} RMSSD {fmt(p.get('rmssd_ms'), '5.1f')}" if p else "      no data       "
    eeg = f"calm {fmt(e.get('calm_score'), '.2f')} FAA {e['faa']:+.3f}" if e else "     no data      "
    cv = f"{c['emotion']:8s} v{c['valence']:+.2f} a{c['arousal']:+.2f}" if c else "no face / no data      "
    return f"POLAR {polar} | EEG {eeg} | CV {cv}"


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"[MONITOR] Connected to {MQTT_HOST} ({reason_code}). Ctrl+C to stop.\n")
    client.subscribe([("telemetry/#", 0), ("affect/state", 0), ("session/state", 1),
                      ("robot/command", 1), ("robot/status", 1)])


def on_message(client, userdata, msg):
    try:
        packet = json.loads(msg.payload)
    except ValueError:
        return
    if not isinstance(packet, dict):
        return
    data = packet.get("data") or {}
    t = f"{time.time() - start:5.0f}s"

    if msg.topic.startswith("telemetry/"):
        source = msg.topic.split("/", 1)[1]
        if source in latest:
            latest[source] = data if packet.get("status") == "ok" else None
    elif msg.topic == "affect/state":
        sources = " ".join(f"{k}:{v}" for k, v in data.get("sources", {}).items())
        print(f"{t}  {sensor_summary()}")
        print(f"        HUB -> valence {fmt(data.get('valence'))}  arousal {fmt(data.get('arousal'))}"
              f"  stress {fmt(data.get('stress'), '.2f')}  => {str(data.get('label', '?')).upper():8s}  [{sources}]")
    elif msg.topic == "session/state":
        print(f"\n{t}  >>> SESSION phase: {data.get('phase')}  (adaptive={data.get('adaptive')})\n")
    elif msg.topic == "robot/command":
        print(f"\n{t}  >>> HUB -> ROBOT: {data.get('action')} [{data.get('animation')}] \"{data.get('text')}\"\n")
    elif msg.topic == "robot/status":
        print(f"{t}  >>> ROBOT status: {data.get('state')} (last command {data.get('last_command_id')})")


def main():
    if USE_WSS:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport="websockets")
        client.tls_set()
    else:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, 30)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("[MONITOR] Stopped.")


if __name__ == "__main__":
    main()
