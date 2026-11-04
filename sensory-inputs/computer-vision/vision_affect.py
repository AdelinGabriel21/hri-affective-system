import cv2
import json
import math
import os
import sys
import threading
import time
from deepface import DeepFace
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "hri.thecraceatree.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", 443))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "telemetry/cv")
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", 0))

# ---------------------------------------------------------
# RUSSELL CIRCUMPLEX 2D COORDINATES
# ---------------------------------------------------------
EMOTION_COORDINATES = {
    "neutral":        {"valence":  0.00, "arousal":  0.00},
    "happy":          {"valence":  0.85, "arousal":  0.45},
    "surprise":       {"valence":  0.45, "arousal":  0.80},
    "disappointment": {"valence": -0.55, "arousal": -0.30},
    "sad":            {"valence": -0.75, "arousal": -0.50},
    "disgust":        {"valence": -0.65, "arousal":  0.30},
    "fear":           {"valence": -0.65, "arousal":  0.85},
    "angry":          {"valence": -0.75, "arousal":  0.75}
}

# ---------------------------------------------------------
# INDIVIDUAL EMOTION TUNING PROFILES
# ---------------------------------------------------------
# Each profile defines:
# - min_threshold: Minimum activation required before considering it dominant.
# - scale_boost: Sensitivity multiplier (compensates for FER model training imbalances).
# - color: BGR color for on-screen HUD.
EMOTION_PROFILES = {
    "neutral": {
        "min_threshold": 0.20,
        "scale_boost": 1.05,
        "color": (200, 200, 200)   # Light Grey
    },
    "happy": {
        "min_threshold": 0.18,
        "scale_boost": 1.15,       # Smiles are reliable; modest boost
        "color": (0, 230, 0)        # Bright Green
    },
    "surprise": {
        "min_threshold": 0.15,
        "scale_boost": 1.10,       # High eyebrow lift + dropped jaw
        "color": (0, 225, 255)      # Yellow
    },
    "disappointment": {
        "min_threshold": 0.16,
        "scale_boost": 1.25,       # Synthesized compound; needs distinct headroom
        "color": (0, 140, 255)      # Amber / Orange
    },
    "sad": {
        "min_threshold": 0.18,
        "scale_boost": 0.85,       # Lowered to prevent resting face false positives
        "color": (255, 140, 0)      # Deep Cyan / Blue
    },
    "disgust": {
        "min_threshold": 0.14,
        "scale_boost": 1.00,       # Nose wrinkle / asymmetric sneer
        "color": (0, 160, 100)      # Olive Green
    },
    "fear": {
        "min_threshold": 0.12,
        "scale_boost": 1.05,       # Wide eyes + tense mouth
        "color": (180, 0, 255)      # Magenta
    },
    "angry": {
        "min_threshold": 0.18,
        "scale_boost": 0.95,       # Dampened brow-furrow bias during focus
        "color": (0, 0, 255)        # Crimson Red
    }
}

SMOOTHING_ALPHA = 0.40  # Temporal filter reactivity (0.0=frozen, 1.0=raw jitter)

# Shared memory
latest_frame = None
is_running = True
frame_lock = threading.Lock()

smoothed_valence = 0.0
smoothed_arousal = 0.0
dominant_emotion = "neutral"
dominant_confidence = 1.0
current_probs = {emo: 0.0 for emo in EMOTION_PROFILES}

def create_mqtt_client():
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, 
        client_id="cv-affect-sensor", 
        transport="websockets"
    )
    if MQTT_PORT == 443:
        client.tls_set()
    
    print(f"[MQTT] Connecting to wss://{MQTT_HOST}:{MQTT_PORT}...")
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
        print("[MQTT] Connected to broker.")
    except Exception as e:
        print(f"[MQTT WARNING] Broker unreachable ({e}). Continuing in local preview mode.")
    return client

def calibrate_and_disambiguate(raw):
    """
    Fine-tunes the raw probabilities by applying individual emotion curves,
    synthesizing disappointment, and resolving boundary conflicts.
    """
    # 1. Base normalization to 0.0 - 1.0
    p = {k: max(raw.get(k, 0.0) / 100.0, 1e-5) for k in EMOTION_PROFILES if k != "disappointment"}

    sad = p.get("sad", 0.0)
    surprise = p.get("surprise", 0.0)
    disgust = p.get("disgust", 0.0)
    fear = p.get("fear", 0.0)
    angry = p.get("angry", 0.0)

    # 2. Compound Disappointment Synthesis
    # Unmet expectation (Sad + Surprise) or Resignation (Sad + Disgust), suppressed by active rage (Angry)
    unmet_expectation = (sad * 0.55) + (surprise * 0.40)
    resignation = (sad * 0.60) + (disgust * 0.40)
    disappoint_raw = max(unmet_expectation, resignation) * max(0.0, 1.0 - (angry * 1.4))

    if sad > 0.12 and (surprise > 0.08 or disgust > 0.08) and angry < 0.30:
        p["disappointment"] = min(0.85, disappoint_raw)
        # Proportionally offload subcomponents to prevent double-counting
        p["sad"] = max(0.0, sad - (p["disappointment"] * 0.50))
        p["surprise"] = max(0.0, surprise - (p["disappointment"] * 0.35))
        p["disgust"] = max(0.0, disgust - (p["disappointment"] * 0.35))
    else:
        p["disappointment"] = 0.0

    # 3. Fine-Tuned Boundary Arbitration

    # A) Fear vs. Surprise: Both have eye-widening. Fear has lip tension & brow pinch.
    if p["fear"] > 0.15 and p["surprise"] > 0.15:
        if angry > 0.10 or sad > 0.10:  # Negative tension indicators point to fear
            p["fear"] *= 1.25
            p["surprise"] *= 0.75
        else:
            p["surprise"] *= 1.20
            p["fear"] *= 0.80

    # B) Angry vs. Disgust: Angry has full brow-furrow; Disgust has upper lip/nasal raise.
    if p["angry"] > 0.15 and p["disgust"] > 0.15:
        if p["disgust"] > (p["angry"] * 0.85):
            p["disgust"] *= 1.20
            p["angry"] *= 0.80

    # C) Sad vs. Angry: Prevents concentrated resting face from turning Angry into Sad.
    if p["sad"] > 0.15 and p["angry"] > 0.15:
        if angry > (sad * 0.90):
            p["angry"] *= 1.20
            p["sad"] *= 0.75

    # 4. Apply Profile Scaling & Thresholding
    scaled = {}
    for emo, score in p.items():
        profile = EMOTION_PROFILES.get(emo, {"scale_boost": 1.0, "min_threshold": 0.10})
        # Score must exceed minimal activation threshold to register
        effective_score = score * profile["scale_boost"] if score >= profile["min_threshold"] else score * 0.5
        scaled[emo] = effective_score

    # 5. Softmax/Sum Normalization
    total = sum(scaled.values())
    for emo in scaled:
        scaled[emo] = round(scaled[emo] / total, 3) if total > 0 else 0.0

    top_emo = max(scaled, key=scaled.get)
    return scaled, top_emo, scaled[top_emo]

def calculate_circumplex(probs):
    total_val = 0.0
    total_aro = 0.0

    for emotion, weight in probs.items():
        if emotion in EMOTION_COORDINATES:
            total_val += weight * EMOTION_COORDINATES[emotion]["valence"]
            total_aro += weight * EMOTION_COORDINATES[emotion]["arousal"]

    val = max(-1.0, min(1.0, total_val))
    aro = max(-1.0, min(1.0, total_aro))
    return val, aro

def inference_worker(mqtt_client):
    global smoothed_valence, smoothed_arousal, dominant_emotion, dominant_confidence, current_probs, is_running

    while is_running:
        frame_copy = None
        with frame_lock:
            if latest_frame is not None:
                frame_copy = latest_frame.copy()

        if frame_copy is not None:
            try:
                analysis = DeepFace.analyze(
                    img_path=frame_copy,
                    actions=['emotion'],
                    enforce_detection=False,
                    detector_backend='opencv',
                    silent=True
                )

                if analysis and len(analysis) > 0:
                    top_face = analysis[0]
                    raw_emotions = top_face.get('emotion', {})

                    # Fine-tuned calibration pipeline
                    probs, top_emo, top_conf = calibrate_and_disambiguate(raw_emotions)
                    current_probs = probs

                    raw_val, raw_aro = calculate_circumplex(probs)

                    # Temporal smoothing
                    smoothed_valence = round(SMOOTHING_ALPHA * raw_val + (1.0 - SMOOTHING_ALPHA) * smoothed_valence, 2)
                    smoothed_arousal = round(SMOOTHING_ALPHA * raw_aro + (1.0 - SMOOTHING_ALPHA) * smoothed_arousal, 2)
                    dominant_emotion = top_emo
                    dominant_confidence = round(top_conf, 2)

                    payload = {
                        "source": "computer_vision",
                        "valence": smoothed_valence,
                        "arousal": smoothed_arousal,
                        "dominant_emotion": dominant_emotion,
                        "confidence": dominant_confidence,
                        "distribution": current_probs,
                        "timestamp": time.time()
                    }
                    mqtt_client.publish(MQTT_TOPIC, json.dumps(payload))
            except Exception:
                pass

        time.sleep(0.20)

def open_webcam(camera_idx):
    if sys.platform == "win32":
        cap = cv2.VideoCapture(camera_idx, cv2.CAP_DSHOW)
    elif sys.platform == "darwin":
        cap = cv2.VideoCapture(camera_idx, cv2.CAP_AVFOUNDATION)
    else:
        cap = cv2.VideoCapture(camera_idx, cv2.CAP_V4L2)

    if not cap.isOpened():
        cap = cv2.VideoCapture(camera_idx)

    if not cap.isOpened():
        return None

    for _ in range(5):
        cap.read()
        time.sleep(0.03)

    return cap

def draw_hud(frame):
    """Renders real-time emotion telemetry bars and Circumplex HUD."""
    h, w, _ = frame.shape
    active_color = EMOTION_PROFILES.get(dominant_emotion, {}).get("color", (200, 200, 200))

    # Top Status Banner
    status_text = f"{dominant_emotion.upper()} ({dominant_confidence * 100:.0f}%)"
    coords_text = f"Valence: {smoothed_valence:+.2f} | Arousal: {smoothed_arousal:+.2f}"

    cv2.putText(frame, status_text, (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.80, active_color, 2)
    cv2.putText(frame, coords_text, (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 2)

    # Mini Probability Histogram (Top Right)
    bar_start_x = w - 170
    bar_start_y = 20
    row_height = 16

    for i, (emo, score) in enumerate(current_probs.items()):
        y = bar_start_y + (i * row_height)
        color = EMOTION_PROFILES.get(emo, {}).get("color", (150, 150, 150))

        # Emotion Name
        cv2.putText(frame, emo[:4], (bar_start_x - 38, y + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
        
        # Background bar
        cv2.rectangle(frame, (bar_start_x, y), (bar_start_x + 100, y + 10), (40, 40, 40), -1)
        # Score bar
        fill_w = int(score * 100)
        cv2.rectangle(frame, (bar_start_x, y), (bar_start_x + fill_w, y + 10), color, -1)

    cv2.putText(frame, "Press 'q' or 'ESC' to exit", (20, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (170, 170, 170), 1)

def main():
    global latest_frame, is_running

    mqtt_client = create_mqtt_client()
    cap = open_webcam(CAMERA_INDEX)

    if cap is None:
        print(f"[FATAL] Camera index {CAMERA_INDEX} unreachable.")
        return

    window_name = "Affective Computing - Fine-Tuned Emotion Monitor"
    cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)

    worker = threading.Thread(target=inference_worker, args=(mqtt_client,), daemon=True)
    worker.start()

    print("[CV] Fine-tuned pipeline active. Look at the camera.")

    try:
        while is_running:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.02)
                continue

            with frame_lock:
                latest_frame = frame

            draw_hud(frame)
            cv2.imshow(window_name, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in [ord('q'), ord('Q'), 27]:
                break
            if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                break

    except KeyboardInterrupt:
        pass
    finally:
        is_running = False
        cap.release()
        cv2.destroyAllWindows()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        print("[CV] Clean exit completed.")

if __name__ == "__main__":
    main()