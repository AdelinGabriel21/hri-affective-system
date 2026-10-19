import json
import os
import time
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

BROKER_HOST = os.getenv("MQTT_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_PORT", 1883))

# Cache cu ultimele valori recepționate
state = {
    "cv": {"valence": 0.0, "arousal": 0.0, "last_seen": 0},
    "polar": {"stress_index": 0.0, "bpm": 70, "last_seen": 0},
    "eeg": {"valence": 0.0, "stress_score": 0.0, "last_seen": 0}
}

last_robot_command_time = 0

def on_connect(client, userdata, flags, rc, properties=None):
    print(f"[FUSION] Conectat la broker cu codul {rc}. Abonare la topicuri...")
    client.subscribe("telemetry/#")

def on_message(client, userdata, msg):
    global last_robot_command_time
    try:
        payload = json.loads(msg.payload.decode())
        topic = msg.topic
        now = time.time()

        if topic == "telemetry/cv":
            state["cv"]["valence"] = payload.get("valence", 0.0)
            state["cv"]["arousal"] = payload.get("arousal", 0.0)
            state["cv"]["last_seen"] = now

        elif topic == "telemetry/polar":
            state["polar"]["stress_index"] = payload.get("stress_index", 0.0)
            state["polar"]["bpm"] = payload.get("bpm", 70)
            state["polar"]["last_seen"] = now

        elif topic == "telemetry/eeg":
            # FAA pozitiv indica o abordare pozitiva (Valence ridicat)
            faa = payload.get("faa", 0.0)
            state["eeg"]["valence"] = max(-1.0, min(1.0, faa))
            state["eeg"]["stress_score"] = payload.get("stress_score", 0.0)
            state["eeg"]["last_seen"] = now

        # Calcul fuziune daca avem date recente
        # Ponderi: CV are greutate mare pe Valence, Polar pe Arousal, EEG pe ambele
        fused_valence = round(0.6 * state["cv"]["valence"] + 0.4 * state["eeg"]["valence"], 2)
        fused_arousal = round(0.5 * state["polar"]["stress_index"] + 0.3 * state["cv"]["arousal"] + 0.2 * state["eeg"]["stress_score"], 2)

        # Determinare stare discreta
        if fused_arousal > 0.65 and fused_valence < -0.2:
            mood = "stressed"
        elif fused_valence > 0.3:
            mood = "calm_positive"
        elif fused_arousal < 0.3:
            mood = "relaxed"
        else:
            mood = "neutral"

        affect_packet = {
            "valence": fused_valence,
            "arousal": fused_arousal,
            "mood": mood,
            "timestamp": now
        }
        client.publish("affect/state", json.dumps(affect_packet))

        # Logica reactiva pentru Robotul NAO (cooldown de minim 10 secunde intre comenzi)
        if mood == "stressed" and (now - last_robot_command_time > 10):
            last_robot_command_time = now
            robot_cmd = {
                "action": "SOOTHE",
                "speech": "Observ ca nivelul tau de tensiune a crescut. Fa o pauza scurta si respira adanc.",
                "posture": "open_arms",
                "timestamp": now
            }
            print(f"[DECISION] Triggering NAO action: {robot_cmd['action']}")
            client.publish("robot/command", json.dumps(robot_cmd))

    except Exception as e:
        print(f"[EROARE FUSION]: {e}")

def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fusion-engine")
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(BROKER_HOST, BROKER_PORT, 60)
    print("[FUSION] Motorul de fuziune ruleaza...")
    client.loop_forever()

if __name__ == "__main__":
    main()