"""Mock telemetry generator (contract v1, see docs/protocol_spec.md).

Publishes contract-valid fake sensor data with "mock": true, so the app, the robot
and the hub can be developed without hardware. Mock only the sensors you are missing:

    python mock_generator.py                    # sources from MOCK_SOURCES (default: all)
    python mock_generator.py --sources eeg,cv   # real Polar runs, EEG and CV are faked

Do not mock a source while its real sensor is publishing; the hub would mix both.
"""
import argparse
import json
import math
import os
import random
import time
import uuid

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

BROKER_HOST = os.getenv("MQTT_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_PORT", 1883))
USE_WSS = os.getenv("MQTT_USE_WSS", "false").lower() == "true"
MQTT_USERNAME = os.getenv("MQTT_USERNAME") or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD") or None

CONTRACT_VERSION = 1
ALL_SOURCES = ("polar", "eeg", "cv")
DEVICES = {"polar": "mock_polar_verity_sense", "eeg": "mock_neurosity_crown", "cv": "mock_webcam"}

PUBLISH_SECONDS = 1.0
EPISODE_PERIOD = 120           # a stress episode every 2 minutes...
EPISODE_START, EPISODE_END = 60, 80   # ...from second 60 to 80 of each period
CV_DROPOUT = 0.05              # chance per message that the face is "lost"


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def in_episode(t):
    return EPISODE_START <= t % EPISODE_PERIOD < EPISODE_END


def latent_state(t):
    """Hidden "true" (valence, arousal) that drives every mocked sensor, so they agree.

    Slow drift plus a stress episode; the first 60 s are calm, which the hub uses as baseline.
    """
    valence = 0.25 * math.sin(t / 55 + 1.0)
    arousal = 0.25 * math.sin(t / 40)
    if in_episode(t):
        valence -= 0.6
        arousal += 0.6
    return clamp(valence), clamp(arousal)


def build_polar(valence, arousal):
    hr = 72 + 18 * arousal + random.gauss(0, 1.5)
    beats = max(1, round(hr / 60 * PUBLISH_SECONDS))
    rr = [round(60000 / hr + random.gauss(0, 25)) for _ in range(beats)]
    return "ok", {
        "hr_bpm": round(hr, 1),
        "rr_ms": rr,
        "rmssd_ms": round(max(5.0, 45 - 20 * arousal + random.gauss(0, 3)), 1),
        "contact": True,
    }


def build_eeg(valence, arousal):
    alpha_left = 10 + random.gauss(0, 0.5)
    faa = 0.12 * valence + random.gauss(0, 0.02)
    alpha_right = alpha_left * math.exp(faa)
    return "ok", {
        "bands": {
            "delta": round(3.0 + random.gauss(0, 0.3), 2),
            "theta": round(6.0 + random.gauss(0, 0.3), 2),
            "alpha": round((alpha_left + alpha_right) / 2, 2),
            "beta": round(14 + 4 * arousal + random.gauss(0, 0.8), 2),
            "gamma": round(4.0 + random.gauss(0, 0.3), 2),
        },
        "alpha_left": round(alpha_left, 2),
        "alpha_right": round(alpha_right, 2),
        "faa": round(math.log(alpha_right) - math.log(alpha_left), 3),
        "calm_score": round(clamp(0.5 - 0.3 * arousal + random.gauss(0, 0.03), 0.0, 1.0), 2),
        "focus_score": round(clamp(0.55 + random.gauss(0, 0.05), 0.0, 1.0), 2),
    }


def build_cv(valence, arousal):
    if random.random() < CV_DROPOUT:
        return "no_signal", None
    v = clamp(valence + random.gauss(0, 0.08))
    a = clamp(arousal + random.gauss(0, 0.08))
    if v > 0.25:
        emotion = "happy"
    elif v < -0.25:
        emotion = "fear" if a > 0.3 else "sad"
    else:
        emotion = "neutral"
    return "ok", {
        "face_detected": True,
        "valence": round(v, 2),
        "arousal": round(a, 2),
        "emotion": emotion,
        "emotion_confidence": round(random.uniform(0.6, 0.95), 2),
        "gaze_on_target": random.random() < 0.75 - 0.3 * max(0.0, arousal),  # stressed users look away more
    }


BUILDERS = {"polar": build_polar, "eeg": build_eeg, "cv": build_cv}


def envelope(source, status, data):
    return {
        "v": CONTRACT_VERSION,
        "source": source,
        "device": DEVICES[source],
        "timestamp": time.time(),
        "status": status,
        "mock": True,
        "data": data,
    }


def parse_sources(text):
    sources = [s.strip() for s in text.split(",") if s.strip()]
    unknown = [s for s in sources if s not in ALL_SOURCES]
    if unknown or not sources:
        raise SystemExit(f"[MOCK] Unknown or empty sources {unknown}. Choose from: {', '.join(ALL_SOURCES)}")
    return sources


def create_client():
    client_id = f"mock-simulator-{uuid.uuid4().hex[:6]}"  # unique, so two teammates can mock at once
    if USE_WSS:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, transport="websockets")
        client.tls_set()
    else:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    scheme = "wss" if USE_WSS else "tcp"
    print(f"[MOCK] Connecting to {scheme}://{BROKER_HOST}:{BROKER_PORT}...")
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()
    return client


def main():
    parser = argparse.ArgumentParser(description="Publish mock telemetry following contract v1.")
    parser.add_argument("--sources", default=os.getenv("MOCK_SOURCES", ",".join(ALL_SOURCES)),
                        help="comma-separated subset of: polar,eeg,cv")
    sources = parse_sources(parser.parse_args().sources)

    client = create_client()
    print(f"[MOCK] Publishing mock telemetry for: {', '.join(sources)}. Ctrl+C to stop.")
    start = time.time()
    try:
        while True:
            t = time.time() - start
            valence, arousal = latent_state(t)
            for source in sources:
                status, data = BUILDERS[source](valence, arousal)
                client.publish(f"telemetry/{source}", json.dumps(envelope(source, status, data)))
            note = "  << stress episode" if in_episode(t) else ""
            print(f"[MOCK] t={t:5.0f}s  valence={valence:+.2f}  arousal={arousal:+.2f}{note}")
            time.sleep(PUBLISH_SECONDS)
    except KeyboardInterrupt:
        print("[MOCK] Stopping generator.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
