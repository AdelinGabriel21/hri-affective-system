import json
import math
import os
import random
import time

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from neurosity import NeurositySDK

load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "hri.thecraceatree.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "443"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "telemetry/eeg")
USE_WSS = os.getenv("MQTT_USE_WSS", "true").lower() == "true"

DEVICE_ID = os.getenv("NEUROSITY_DEVICE_ID", "").strip()
EMAIL = os.getenv("NEUROSITY_EMAIL", "").strip()
PASSWORD = os.getenv("NEUROSITY_PASSWORD", "")

F5 = 2
F6 = 5
MAX_AGE_SECONDS = 3

latest = {
    "bands": None,
    "bands_time": 0,
    "calm": None,
    "focus": None,
    "quality": None,
}


def on_bands(message):
    latest["bands"] = message["data"]
    latest["bands_time"] = time.time()


def on_calm(message):
    latest["calm"] = message["probability"]


def on_focus(message):
    latest["focus"] = message["probability"]


def on_quality(message):
    latest["quality"] = message


def create_mqtt_client():
    if USE_WSS:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="eeg-crown-client", transport="websockets")
        client.tls_set()
    else:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="eeg-crown-client")
    print(f"[MQTT] Conectare la {MQTT_HOST}:{MQTT_PORT}...")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    return client


def connect_neurosity():
    sdk = NeurositySDK({"device_id": DEVICE_ID})
    sdk.login({"email": EMAIL, "password": PASSWORD})
    sdk.brainwaves_power_by_band(on_bands)
    sdk.calm(on_calm)
    sdk.focus(on_focus)
    sdk.signal_quality(on_quality)
    return sdk


def calculate_faa(alpha_left, alpha_right):
    """
    Calculeaza Frontal Alpha Asymmetry (FAA).
    FAA = ln(Alpha_Right) - ln(Alpha_Left)
    Valoare pozitiva = abordare/stare pozitiva (Valence ridicat).
    Valoare negativa = retragere/anxietate (Valence scazut).
    """
    return math.log(max(0.001, alpha_right)) - math.log(max(0.001, alpha_left))


def average(values):
    return sum(values) / len(values)


def build_message(status, data):
    return {
        "v": 1,
        "source": "eeg",
        "device": DEVICE_ID or "crown-sim",
        "timestamp": time.time(),
        "status": status,
        "mock": not DEVICE_ID,
        "data": data,
    }


def publish_no_signal(client):
    message = build_message("no_signal", None)
    client.publish(MQTT_TOPIC, json.dumps(message))


def get_real_data():
    bands = latest["bands"]
    quality = latest["quality"]

    if bands is None or quality is None:
        return "no_signal", None

    if time.time() - latest["bands_time"] > MAX_AGE_SECONDS:
        return "no_signal", None

    if quality[F5]["status"] == "bad" or quality[F6]["status"] == "bad":
        return "no_signal", None

    alpha_left = bands["alpha"][F5]
    alpha_right = bands["alpha"][F6]
    faa = calculate_faa(alpha_left, alpha_right)

    calm = latest["calm"]
    focus = latest["focus"]
    if calm is not None:
        calm = round(calm, 2)
    if focus is not None:
        focus = round(focus, 2)

    data = {
        "bands": {
            "delta": round(average(bands["delta"]), 2),
            "theta": round(average(bands["theta"]), 2),
            "alpha": round(average(bands["alpha"]), 2),
            "beta": round(average(bands["beta"]), 2),
            "gamma": round(average(bands["gamma"]), 2),
        },
        "alpha_left": round(alpha_left, 2),
        "alpha_right": round(alpha_right, 2),
        "faa": round(faa, 3),
        "calm_score": calm,
        "focus_score": focus,
    }
    return "ok", data


def get_mock_data(step):
    alpha_left = 10.0 + 2.0 * math.sin(step) + random.uniform(-0.5, 0.5)
    alpha_right = 11.0 + 2.0 * math.cos(step) + random.uniform(-0.5, 0.5)
    delta = 20.0 + random.uniform(-1.0, 1.0)
    theta = 6.0 + random.uniform(-0.3, 0.3)
    alpha = (alpha_left + alpha_right) / 2
    beta = 15.0 + 3.0 * math.sin(step * 1.5) + random.uniform(-0.8, 0.8)
    gamma = 4.0 + random.uniform(-0.3, 0.3)

    faa = calculate_faa(alpha_left, alpha_right)
    calm = max(0.0, min(1.0, 0.5 + 0.4 * math.sin(step / 3)))
    focus = max(0.0, min(1.0, beta / (alpha + theta)))

    data = {
        "bands": {
            "delta": round(delta, 2),
            "theta": round(theta, 2),
            "alpha": round(alpha, 2),
            "beta": round(beta, 2),
            "gamma": round(gamma, 2),
        },
        "alpha_left": round(alpha_left, 2),
        "alpha_right": round(alpha_right, 2),
        "faa": round(faa, 3),
        "calm_score": round(calm, 2),
        "focus_score": round(focus, 2),
    }
    return "ok", data


def main():
    client = create_mqtt_client()
    print("[EEG] Pornire modul Neurosity Crown...")

    sdk = None
    if DEVICE_ID:
        print("[EEG] Conectare la casca Neurosity Crown...")
        sdk = connect_neurosity()
    else:
        print("[MOCK EEG] Nu s-a gasit NEUROSITY_DEVICE_ID in .env. Rulam in mod SIMULATOR...")

    step = 0
    try:
        while True:
            step += 0.2

            if sdk:
                status, data = get_real_data()
            else:
                status, data = get_mock_data(step)

            if status == "no_signal":
                publish_no_signal(client)
                print("[EEG] no_signal - verifica casca")
            else:
                message = build_message(status, data)
                client.publish(MQTT_TOPIC, json.dumps(message))
                print(f"[EEG] FAA: {data['faa']} | Calm: {data['calm_score']} | Focus: {data['focus_score']}")

            time.sleep(1.0)

    except KeyboardInterrupt:
        print("[EEG] Oprire modul.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()