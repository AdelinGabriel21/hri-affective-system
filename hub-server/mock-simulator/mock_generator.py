import json
import math
import os
import random
import time
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

BROKER_HOST = os.getenv("MQTT_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_PORT", 1883))

def create_client():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mock-simulator")
    print(f"[MOCK] Conectare la broker {BROKER_HOST}:{BROKER_PORT}...")
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()
    return client

def main():
    client = create_client()
    step = 0

    print("[MOCK] Generatorul a pornit. Publica telemetrie pe MQTT...")

    try:
        while True:
            t = time.time()
            step += 0.1

            # 1. Simulare Polar Verity Sense (BPM, RMSSD, Stres)
            # Oscilatie realista a pulsului intre 65 si 85 bpm
            base_hr = 75 + 8 * math.sin(step * 0.2) + random.uniform(-1.5, 1.5)
            rmssd = 45 - 10 * math.sin(step * 0.2) + random.uniform(-3, 3)
            stress_polar = max(0.0, min(1.0, (100 - rmssd) / 100))

            polar_payload = {
                "source": "polar_verity_sense",
                "bpm": round(base_hr, 1),
                "rmssd_ms": round(rmssd, 2),
                "stress_index": round(stress_polar, 2),
                "timestamp": t
            }
            client.publish("telemetry/polar", json.dumps(polar_payload))

            # 2. Simulare Computer Vision (Valence & Arousal facial)
            # Valence oscileaza intre -0.5 si 0.6; Arousal intre 0.1 si 0.7
            cv_val = 0.2 + 0.4 * math.sin(step * 0.15) + random.uniform(-0.05, 0.05)
            cv_aro = 0.3 + 0.3 * math.cos(step * 0.1) + random.uniform(-0.05, 0.05)
            dominant = "happy" if cv_val > 0.2 else ("stressed" if cv_aro > 0.5 else "neutral")

            cv_payload = {
                "source": "computer_vision",
                "valence": round(max(-1.0, min(1.0, cv_val)), 2),
                "arousal": round(max(-1.0, min(1.0, cv_aro)), 2),
                "dominant_emotion": dominant,
                "confidence": 0.94,
                "timestamp": t
            }
            client.publish("telemetry/cv", json.dumps(cv_payload))

            # 3. Simulare Casca EEG Crown (Alpha, Beta, FAA, Focus)
            alpha_power = 12.0 + 3.0 * math.cos(step * 0.25)
            beta_power = 16.0 + 4.0 * math.sin(step * 0.25)
            faa = round((alpha_power - 10) / 10 + random.uniform(-0.05, 0.05), 2)
            focus = round(max(0.0, min(1.0, beta_power / (alpha_power + beta_power))), 2)

            eeg_payload = {
                "source": "eeg_crown",
                "bands": {
                    "theta": round(6.5 + random.uniform(-0.5, 0.5), 2),
                    "alpha": round(alpha_power, 2),
                    "beta": round(beta_power, 2)
                },
                "faa": faa,
                "focus_score": focus,
                "stress_score": round(max(0.0, min(1.0, 1.0 - (alpha_power / 20.0))), 2),
                "timestamp": t
            }
            client.publish("telemetry/eeg", json.dumps(eeg_payload))

            time.sleep(1.0)  # Emite la fiecare secunda
    except KeyboardInterrupt:
        print("[MOCK] Oprire generator.")
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()