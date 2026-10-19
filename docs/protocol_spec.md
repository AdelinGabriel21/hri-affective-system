# Specificație Contract MQTT (HRI Multimodal System)

Broker endpoint public: `wss://hri.thecraceatree.com` (port 443 / WSS)  
Broker endpoint local LXC: `192.168.1.220` (port 1883 / TCP)

## Canale de Telemetrie (Publică senzorii)
* **`telemetry/polar`**:
  `{"bpm": 74, "rmssd": 45.2, "stress_index": 0.32, "ts": 1729350000}`
* **`telemetry/cv`**:
  `{"valence": 0.45, "arousal": 0.20, "dominant_emotion": "happy", "confidence": 0.95, "ts": 1729350000}`
* **`telemetry/eeg`**:
  `{"bands": {"alpha": 12.3, "beta": 18.5}, "faa": 0.12, "stress": 0.4, "ts": 1729350000}`

## Canale de Fuziune & Comandă (Publică Hub-ul / Consumă Mobilul & Robotul)
* **`affect/state`**:
  `{"valence": -0.3, "arousal": 0.7, "dominant_emotion": "stressed", "robot_action": "CALM"}`
* **`robot/command`**:
  `{"action": "SPEAK", "speech": "Incearca sa te relaxezi", "gesture": "open_arms"}`