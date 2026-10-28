import json
import os
import time
import math
import random
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "hri.thecraceatree.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "443"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "telemetry/eeg")

DEVICE_ID = os.getenv("NEUROSITY_DEVICE_ID", "").strip()

def create_mqtt_client():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="eeg-crown-client", transport="websockets")
    if MQTT_PORT == 443:
        client.tls_set()
    print(f"[MQTT] Conectare la wss://{MQTT_HOST}:{MQTT_PORT}...")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    return client

def calculate_faa(alpha_left, alpha_right):
    """
    Calculeaza Frontal Alpha Asymmetry (FAA).
    FAA = ln(Alpha_Right) - ln(Alpha_Left)
    Valoare pozitiva = abordare/stare pozitiva (Valence ridicat).
    Valoare negativa = retragere/anxietate (Valence scazut).
    """
    try:
        return math.log(max(0.001, alpha_right)) - math.log(max(0.001, alpha_left))
    except Exception:
        return 0.0

def main():
    mqtt_client = create_mqtt_client()
    print("[EEG] Pornire modul Neurosity Crown...")

    # Daca avem credentilale completate, incercam conectarea reala
    if DEVICE_ID:
        print(f"[EEG] Conectare la casca Neurosity Crown ID: {DEVICE_ID}...")
        # AICI se va initializa SDK-ul Neurosity cand esti la laborator
        # Ex: neurosity.login(...) si neurosity.brainwaves_power_by_band()
    else:
        print("[MOCK EEG] Nu s-a gasit NEUROSITY_DEVICE_ID in .env. Rulam in mod SIMULATOR...")

    try:
        step = 0
        while True:
            t = time.time()
            step += 0.2

            # Generare valori plauzibile pentru testare locala
            alpha_l = 10.0 + 2.0 * math.sin(step) + random.uniform(-0.5, 0.5)
            alpha_r = 11.0 + 2.0 * math.cos(step) + random.uniform(-0.5, 0.5)
            beta = 15.0 + 3.0 * math.sin(step * 1.5) + random.uniform(-0.8, 0.8)
            theta = 6.0 + random.uniform(-0.3, 0.3)

            faa = round(calculate_faa(alpha_l, alpha_r), 3)
            # Focus score bazat pe raportul Beta / (Alpha + Theta)
            focus = round(max(0.0, min(1.0, beta / (alpha_l + theta))), 2)
            stress = round(max(0.0, min(1.0, 1.0 - (alpha_r / 20.0))), 2)

            payload = {
                "source": "neurosity_crown",
                "bands": {
                    "theta": round(theta, 2),
                    "alpha_left": round(alpha_l, 2),
                    "alpha_right": round(alpha_r, 2),
                    "beta": round(beta, 2)
                },
                "faa": faa,
                "focus_score": focus,
                "stress_score": stress,
                "timestamp": t
            }

            mqtt_client.publish(MQTT_TOPIC, json.dumps(payload))
            print(f"[EEG] Alpha L/R: {alpha_l:.1f}/{alpha_r:.1f} | FAA: {faa:<6} | Focus: {focus:.2f} | Stres: {stress:.2f}")
            time.sleep(1.0)

    except KeyboardInterrupt:
        print("[EEG] Oprire modul.")
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

if __name__ == "__main__":
    main()