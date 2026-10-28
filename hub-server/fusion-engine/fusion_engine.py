"""Multimodal fusion engine (contract v1, see docs/protocol_spec.md).

Subscribes to telemetry/#, robot/status and session/control.
Publishes affect/state at a fixed rate, robot/command when the robot should react,
and session/state (retained) whenever the session changes.
"""
import json
import math
import os
import threading
import time
import uuid
from datetime import datetime

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

BROKER_HOST = os.getenv("MQTT_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_PORT", 1883))
USE_WSS = os.getenv("MQTT_USE_WSS", "false").lower() == "true"
MQTT_USERNAME = os.getenv("MQTT_USERNAME") or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD") or None

CONTRACT_VERSION = 1
SOURCES = ("polar", "eeg", "cv")

TICK_SECONDS = 1.0             # affect/state publish period
STALE_SECONDS = 3.0            # a source silent for longer than this is "stale"
BASELINE_SECONDS = float(os.getenv("BASELINE_SECONDS", 60))
SMOOTHING_ALPHA = 0.3          # EMA on the fused output (1.0 = no smoothing)
FAA_SCALE = 0.2                # FAA of +-0.2 maps to roughly +-0.76 valence
ROBOT_COOLDOWN_SECONDS = 20
STRESS_TICKS_TO_REACT = 3      # consecutive "stressed" ticks before the robot reacts

# Per-source weights, renormalized over the sources usable at each tick
VALENCE_WEIGHTS = {"cv": 0.6, "eeg": 0.4}
AROUSAL_WEIGHTS = {"polar": 0.5, "cv": 0.3, "eeg": 0.2}


class Baseline:
    """Collects resting samples per feature, then converts later values to z-scores.

    The collection window starts with the first sample, so it also works when
    sensors are started after the hub. A new Baseline is created per session.
    """
    MIN_STD = {"hr_bpm": 3.0, "rmssd_ms": 5.0, "calm_score": 0.05}
    MIN_SAMPLES = 5

    def __init__(self):
        self.started_at = None
        self.samples = {}
        self.stats = None  # {feature: (mean, std)} once frozen

    @property
    def ready(self):
        return self.stats is not None

    def add(self, feature, value):
        if self.ready or value is None:
            return
        if self.started_at is None:
            self.started_at = time.time()
        self.samples.setdefault(feature, []).append(value)

    def update(self, now):
        if self.ready or self.started_at is None or now - self.started_at < BASELINE_SECONDS:
            return
        self.stats = {}
        for feature, xs in self.samples.items():
            if len(xs) < self.MIN_SAMPLES:
                continue
            mean = sum(xs) / len(xs)
            std = math.sqrt(sum((x - mean) ** 2 for x in xs) / len(xs))
            self.stats[feature] = (mean, max(std, self.MIN_STD.get(feature, 1e-3)))
        summary = ", ".join(f"{f}={m:.2f}+-{s:.2f}" for f, (m, s) in self.stats.items()) or "no features"
        print(f"[FUSION] Baseline ready: {summary}")

    def z(self, feature, value):
        if value is None or not self.stats or feature not in self.stats:
            return None
        mean, std = self.stats[feature]
        return (value - mean) / std


# Shared state, touched by the MQTT network thread and the tick loop
lock = threading.Lock()
sources = {s: {"status": "missing", "data": None, "last_seen": 0.0} for s in SOURCES}
baseline = Baseline()
session = None                 # dict while a session exists, see publish_session_state()
robot_state = "offline"
fused = {"valence": None, "arousal": None}
stressed_ticks = 0
last_robot_command_time = 0.0
command_counter = 0
warned_topics = set()


def envelope(data, status="ok"):
    return {
        "v": CONTRACT_VERSION,
        "source": "hub",
        "device": "fusion_engine",
        "timestamp": time.time(),
        "status": status,
        "mock": False,
        "data": data,
    }


def squash(z):
    """Map an unbounded z-score to -1..1 (z = +-2 gives about +-0.76)."""
    return math.tanh(z / 2)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def weighted_mean(parts):
    total = sum(w for w, _ in parts)
    if total <= 0:
        return None
    return sum(w * x for w, x in parts) / total


def smooth(previous, new):
    if new is None or previous is None:
        return new
    return SMOOTHING_ALPHA * new + (1 - SMOOTHING_ALPHA) * previous


def source_estimates(source, data):
    """Map one source's features to (valence, arousal) in -1..1, None where unavailable."""
    if source == "cv":
        return data.get("valence"), data.get("arousal")

    if source == "polar":
        parts = []
        z_hr = baseline.z("hr_bpm", data.get("hr_bpm"))
        if z_hr is not None:
            parts.append(z_hr)
        z_rmssd = baseline.z("rmssd_ms", data.get("rmssd_ms"))
        if z_rmssd is not None:
            parts.append(-z_rmssd)  # lower HRV than baseline = more aroused
        return None, squash(sum(parts) / len(parts)) if parts else None

    if source == "eeg":
        faa = data.get("faa")
        valence = math.tanh(faa / FAA_SCALE) if faa is not None else None
        z_calm = baseline.z("calm_score", data.get("calm_score"))
        arousal = squash(-z_calm) if z_calm is not None else None
        return valence, arousal

    return None, None


def stress_and_label(valence, arousal):
    """Heuristic: stress is high activation combined with negative valence."""
    if valence is None and arousal is None:
        return None, "unknown"
    stress = 0.6 * max(0.0, arousal or 0.0) + 0.4 * max(0.0, -(valence or 0.0))
    stress = clamp(stress / 0.6 if valence is None else stress, 0.0, 1.0)
    if stress >= 0.6:
        label = "stressed"
    elif stress < 0.25 and (arousal is None or arousal <= 0.0):
        label = "calm"
    else:
        label = "neutral"
    return stress, label


def current_session_id():
    if session and session["phase"] != "finished":
        return session["session_id"]
    return None


def publish_session_state(client):
    data = dict(session) if session else {
        "session_id": None, "phase": "idle", "interview_type": None,
        "adaptive": None, "question_index": None, "question_count": None,
    }
    client.publish("session/state", json.dumps(envelope(data)), qos=1, retain=True)


def send_robot_command(client, action, text=None, animation=None, speech_rate=None):
    global command_counter
    command_counter += 1
    data = {
        "command_id": f"c-{command_counter:04d}",
        "action": action,
        "text": text,
        "animation": animation,
        "speech_rate": speech_rate,
    }
    print(f"[DECISION] robot/command {data['command_id']}: {action} {text!r}")
    client.publish("robot/command", json.dumps(envelope(data)), qos=1)


def handle_telemetry(source, packet):
    if source not in sources:
        return
    entry = sources[source]
    entry["last_seen"] = time.time()
    entry["status"] = packet.get("status", "error")
    data = packet.get("data") if entry["status"] == "ok" else None
    entry["data"] = data
    if not data:
        return
    if source == "polar":
        baseline.add("hr_bpm", data.get("hr_bpm"))
        baseline.add("rmssd_ms", data.get("rmssd_ms"))
    elif source == "eeg":
        baseline.add("calm_score", data.get("calm_score"))


def handle_robot_status(packet):
    global robot_state
    robot_state = (packet.get("data") or {}).get("state", "offline")


def handle_session_control(client, packet):
    global session, baseline
    data = packet.get("data") or {}
    command = data.get("command")

    if command == "start":
        session_id = f"{datetime.now():%Y-%m-%dT%H-%M-%S}_{uuid.uuid4().hex[:4]}"
        session = {
            "session_id": session_id,
            "phase": "baseline",
            "interview_type": data.get("interview_type"),
            "adaptive": bool(data.get("adaptive", True)),
            "question_index": 0,
            "question_count": None,  # set once the question bank exists
        }
        baseline = Baseline()
        print(f"[SESSION] Started {session_id} (adaptive={session['adaptive']}), collecting baseline...")
    elif command == "stop" and session:
        session["phase"] = "finished"
        print(f"[SESSION] Finished {session['session_id']}")
    else:
        print(f"[SESSION] Ignoring unsupported command: {command!r}")
        return
    publish_session_state(client)


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"[FUSION] Connected to broker ({reason_code}). Subscribing...")
    client.subscribe([("telemetry/#", 0), ("robot/status", 1), ("session/control", 1)])
    with lock:
        publish_session_state(client)


def on_message(client, userdata, msg):
    try:
        packet = json.loads(msg.payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        packet = None
    if not isinstance(packet, dict) or packet.get("v") != CONTRACT_VERSION:
        if msg.topic not in warned_topics:
            warned_topics.add(msg.topic)
            print(f"[FUSION] Ignoring messages on {msg.topic} that do not follow contract v{CONTRACT_VERSION}")
        return

    with lock:
        if msg.topic.startswith("telemetry/"):
            handle_telemetry(msg.topic.split("/", 1)[1], packet)
        elif msg.topic == "robot/status":
            handle_robot_status(packet)
        elif msg.topic == "session/control":
            handle_session_control(client, packet)


def maybe_react(client, label, now):
    """Placeholder adaptation: say something calming after sustained stress."""
    global stressed_ticks, last_robot_command_time
    stressed_ticks = stressed_ticks + 1 if label == "stressed" else 0

    # Without a session (connection phase) the robot reacts so the loop can be demoed
    if session is not None and not (session["phase"] == "interview" and session["adaptive"]):
        return
    if stressed_ticks < STRESS_TICKS_TO_REACT or robot_state == "speaking":
        return
    if now - last_robot_command_time < ROBOT_COOLDOWN_SECONDS:
        return

    last_robot_command_time = now
    send_robot_command(
        client, "say",
        text="Take your time. Let's slow down and take a deep breath before we continue.",
        animation="open_arms",
        speech_rate=0.85,
    )


def tick(client):
    now = time.time()
    with lock:
        baseline.update(now)
        if session and session["phase"] == "baseline" and baseline.ready:
            session["phase"] = "interview"
            publish_session_state(client)

        statuses = {}
        valence_parts, arousal_parts = [], []
        for name, entry in sources.items():
            status = entry["status"]
            if status != "missing" and now - entry["last_seen"] > STALE_SECONDS:
                status = "stale"
            statuses[name] = status
            if status != "ok":
                continue
            valence, arousal = source_estimates(name, entry["data"])
            if valence is not None:
                valence_parts.append((VALENCE_WEIGHTS.get(name, 0.0), clamp(valence, -1.0, 1.0)))
            if arousal is not None:
                arousal_parts.append((AROUSAL_WEIGHTS.get(name, 0.0), clamp(arousal, -1.0, 1.0)))

        fused["valence"] = smooth(fused["valence"], weighted_mean(valence_parts))
        fused["arousal"] = smooth(fused["arousal"], weighted_mean(arousal_parts))
        stress, label = stress_and_label(fused["valence"], fused["arousal"])

        affect = {
            "session_id": current_session_id(),
            "valence": None if fused["valence"] is None else round(fused["valence"], 2),
            "arousal": None if fused["arousal"] is None else round(fused["arousal"], 2),
            "stress": None if stress is None else round(stress, 2),
            "label": label,
            "sources": statuses,
        }
        client.publish("affect/state", json.dumps(envelope(affect)))
        maybe_react(client, label, now)


def create_client():
    if USE_WSS:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fusion-engine", transport="websockets")
        client.tls_set()
    else:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fusion-engine")
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    return client


def main():
    client = create_client()
    scheme = "wss" if USE_WSS else "tcp"
    print(f"[FUSION] Connecting to {scheme}://{BROKER_HOST}:{BROKER_PORT}...")
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()
    print("[FUSION] Fusion engine running. Ctrl+C to stop.")
    try:
        while True:
            tick(client)
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        print("[FUSION] Stopping.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
