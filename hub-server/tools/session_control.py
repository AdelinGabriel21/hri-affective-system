"""Start or stop an interview session on the hub, the way the app will later (session/control).

    python session_control.py start                     adaptive session (the robot reacts to stress)
    python session_control.py start --non-adaptive      control condition: same session, robot does not adapt
    python session_control.py stop

Starting a session resets the per-user baseline: the hub records 60 s of rest, then switches to
the interview phase. Broker settings come from .env (see .env.example); the default is the public broker.
"""
import argparse
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


def main():
    parser = argparse.ArgumentParser(description="Send session/control to the hub (contract v1).")
    parser.add_argument("command", choices=["start", "stop"])
    parser.add_argument("--type", default="demo", help="interview_type for start (default: demo)")
    parser.add_argument("--non-adaptive", action="store_true", help="start a session where the robot does not adapt")
    args = parser.parse_args()

    if args.command == "start":
        data = {"command": "start", "interview_type": args.type, "adaptive": not args.non_adaptive}
    else:
        data = {"command": "stop"}
    message = {"v": 1, "source": "app", "device": "session_control", "timestamp": time.time(),
               "status": "ok", "mock": False, "data": data}

    if USE_WSS:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport="websockets")
        client.tls_set()
    else:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.connect(MQTT_HOST, MQTT_PORT, 30)
    client.loop_start()
    client.publish("session/control", json.dumps(message), qos=1).wait_for_publish(5)
    client.loop_stop()
    client.disconnect()
    print(f"[SESSION] Sent {args.command}: {json.dumps(data)}")


if __name__ == "__main__":
    main()
