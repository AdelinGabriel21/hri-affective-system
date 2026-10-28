import cv2
import json
import math
import os
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

# Circumplex 2D Coordinates
EMOTION_COORDINATES = {
    "happy":    {"valence":  0.85, "arousal":  0.45},
    "neutral":  {"valence":  0.00, "arousal":  0.00},
    "surprise": {"valence":  0.40, "arousal":  0.80},
    "sad":      {"valence": -0.75, "arousal": -0.45},
    "fear":     {"valence": -0.65, "arousal":  0.85},
    "angry":    {"valence": -0.75, "arousal":  0.75},
    "disgust":  {"valence": -0.65, "arousal":  0.35}
}

# ---------------------------------------------------------
# TUNING PARAMETERS: CALIBRATION & DISAMBIGUATION
# ---------------------------------------------------------
# DeepFace is notoriously biased toward "angry" during concentration.
# We apply prior weights to balance sensitivity:
EMOTION_PRIOR_WEIGHTS = {
    "angry":    0.75,   # Dampen angry false-positives
    "sad":      1.30,   # Boost subtle sad / downturned lip cues
    "happy":    1.00,
    "neutral":  0.90,   # Avoid sticking permanently on neutral
    "surprise": 1.05,
    "fear":     0.90,
    "disgust":  0.85
}

TEMPERATURE = 0.55       # Sharpens dominant probability peaks
SMOOTHING_ALPHA = 0.40   # EMA smoothing speed (0.1 = slow, 0.9 = instant)

# Shared thread memory
latest_frame = None
is_running = True
frame_lock = threading.Lock()

smoothed_valence = 0.0
smoothed_arousal = 0.0
dominant_emotion = "neutral"
dominant_confidence = 1.0

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

def apply_calibrated_scaling(raw_emotions):
    """
    1. Multiplies raw probabilities by empirical prior weights.
    2. Applies Temperature Scaling to separate subtle differences.
    """
    weighted = {}
    total_w = 0.0

    # Apply prior adjustments
    for emo, score in raw_emotions.items():
        w_factor = EMOTION_PRIOR_WEIGHTS.get(emo, 1.0)
        adjusted = max((score / 100.0) * w_factor, 1e-6)
        weighted[emo] = adjusted
        total_w += adjusted

    for emo in weighted:
        weighted[emo] /= total_w

    # Temperature Sharpening
    scaled = {}
    total_s = 0.0
    for emo, p in weighted.items():
        val = math.exp(math.log(p) / TEMPERATURE)
        scaled[emo] = val
        total_s += val

    for emo in scaled:
        scaled[emo] /= total_s

    return scaled

def disambiguate_sad_vs_angry(scaled_emotions):
    """
    Specific contrastive arbiter for the Sad vs. Angry boundary.
    If both are elevated, resolves based on margin and relative ratio.
    """
    sad_score = scaled_emotions.get("sad", 0.0)
    angry_score = scaled_emotions.get("angry", 0.0)

    # If both negative emotions are competing closely
    if sad_score > 0.15 and angry_score > 0.15:
        # If sad is within 80% of angry score, prioritize sad
        # (models almost always over-trigger angry due to brow furrowing)
        if sad_score >= (angry_score * 0.80):
            scaled_emotions["sad"] *= 1.25
            scaled_emotions["angry"] *= 0.70

    # Pick top emotion after disambiguation
    top_emo = max(scaled_emotions, key=scaled_emotions.get)
    return top_emo, scaled_emotions[top_emo]

def calculate_circumplex(scaled_emotions):
    total_val = 0.0
    total_aro = 0.0

    for emotion, weight in scaled_emotions.items():
        if emotion in EMOTION_COORDINATES:
            total_val += weight * EMOTION_COORDINATES[emotion]["valence"]
            total_aro += weight * EMOTION_COORDINATES[emotion]["arousal"]

    val = max(-1.0, min(1.0, total_val))
    aro = max(-1.0, min(1.0, total_aro))
    return val, aro

def inference_worker(mqtt_client):
    global smoothed_valence, smoothed_arousal, dominant_emotion, dominant_confidence, is_running

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

                    # 1. Calibrate and scale probabilities
                    calibrated = apply_calibrated_scaling(raw_emotions)

                    # 2. Run contrastive resolution for Sad vs. Angry
                    top_emo, top_conf = disambiguate_sad_vs_angry(calibrated)

                    # 3. Continuous 2D Coordinates
                    raw_val, raw_aro = calculate_circumplex(calibrated)

                    # 4. Temporal Smoothing (EMA)
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
                        "timestamp": time.time()
                    }
                    mqtt_client.publish(MQTT_TOPIC, json.dumps(payload))
            except Exception:
                pass

        time.sleep(0.25)

def open_webcam(camera_idx):
    cap = cv2.VideoCapture(camera_idx, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(camera_idx)
    if not cap.isOpened():
        return None

    for _ in range(5):
        cap.read()
        time.sleep(0.04)
    return cap

def main():
    global latest_frame, is_running

    mqtt_client = create_mqtt_client()
    cap = open_webcam(CAMERA_INDEX)

    if cap is None:
        print(f"[FATAL] Camera index {CAMERA_INDEX} unreachable.")
        return

    window_name = "Affective Computing - Facial Monitor"
    cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)

    worker = threading.Thread(target=inference_worker, args=(mqtt_client,), daemon=True)
    worker.start()

    print("[CV] Video stream running. Press 'q' or 'ESC' to exit.")

    try:
        while is_running:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.02)
                continue

            with frame_lock:
                latest_frame = frame

            # Color indicators:
            # Green = Positive, Blue = Sad (Low Arousal, Negative), Red = Angry (High Arousal, Negative)
            if smoothed_valence >= 0:
                color = (0, 220, 0)
            elif dominant_emotion == "sad":
                color = (255, 180, 0)  # Cyan/Blue tint for sadness
            else:
                color = (0, 70, 255)   # Red tint for angry/stress

            status_text = f"Emotion: {dominant_emotion.upper()} ({dominant_confidence * 100:.0f}%)"
            coords_text = f"Valence: {smoothed_valence:+.2f} | Arousal: {smoothed_arousal:+.2f}"

            cv2.putText(frame, status_text, (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2)
            cv2.putText(frame, coords_text, (20, 68), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (230, 230, 230), 2)
            cv2.putText(frame, "Press 'q' or 'ESC' to exit", (20, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (170, 170, 170), 1)

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