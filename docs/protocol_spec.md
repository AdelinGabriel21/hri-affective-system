# MQTT Contract — HRI Multimodal System (v1, draft)

This is the single source of truth for every message on the broker. If your code and this file disagree, fix one of them in the same PR.

## Broker

| Endpoint | Use |
| :--- | :--- |
| `wss://hri.thecraceatree.com:443` | Public WebSocket + TLS. Sensors, app and robot from outside the LAN. |
| `tcp://192.168.1.220:1883` | Plain TCP inside the LAN (hub services in the LXC). |

## General rules

1. **Encoding:** UTF-8 JSON, one object per message.
2. **Envelope:** every message has the envelope fields below. Payload-specific fields go in `data`.
3. **Time:** `timestamp` is Unix time in seconds (float) from the sender's clock. The hub uses its own receive time for freshness, so clock skew between laptops does not break fusion.
4. **No fake values:** if a sensor has nothing valid to report (no face, no skin contact, headset off), it still publishes, with `status` other than `"ok"` and `data` set to `null`. The hub treats that source as missing instead of averaging in zeros.
5. **Ranges (shared across all modules):**
   - `valence`: `-1.0` (negative) … `+1.0` (positive)
   - `arousal`: `-1.0` (calmer than baseline) … `+1.0` (more activated than baseline); `0.0` = the user's baseline
   - any `*_score` / `stress`: `0.0` … `1.0`
   - `null` means "not available", never `0`.
6. **Versioning:** `v` is the contract version. Breaking changes bump it.

### Envelope

```json
{
  "v": 1,
  "source": "polar",
  "device": "polar_verity_sense",
  "timestamp": 1761650000.123,
  "status": "ok",
  "mock": false,
  "data": { }
}
```

| Field | Type | Notes |
| :--- | :--- | :--- |
| `v` | int | Contract version, currently `1`. |
| `source` | string | `"polar"`, `"eeg"`, `"cv"`, `"hub"`, `"robot"`, `"app"`. |
| `device` | string | Free text identifying the hardware/implementation. |
| `timestamp` | float | Unix seconds. |
| `status` | string | `"ok"`, `"no_signal"`, `"calibrating"`, `"error"`. |
| `mock` | bool | `true` when produced by the mock simulator. |
| `data` | object \| null | `null` whenever `status != "ok"`. |

## Topic map

| Topic | Publisher | Subscribers | Rate | QoS | Retained |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `telemetry/polar` | Polar reader | hub | 1 Hz | 0 | no |
| `telemetry/eeg` | Crown reader | hub | 1 Hz | 0 | no |
| `telemetry/cv` | Vision module | hub | 2–4 Hz | 0 | no |
| `affect/state` | hub (fusion) | app, recorder | 1 Hz | 0 | no |
| `robot/command` | hub | NAO bridge | on event | 1 | no |
| `robot/status` | NAO bridge | hub, app | on change | 1 | yes |
| `session/control` | app | hub | on event | 1 | no |
| `session/state` | hub | app, robot | on change | 1 | yes |

## Telemetry (sensors → hub)

Sensors publish **measured features**, plus their own estimates where the device or model provides one. Baseline normalization and fusion happen in the hub, because only the hub knows the session and its baseline.

### `telemetry/polar`

```json
"data": {
  "hr_bpm": 78.0,
  "rr_ms": [812, 790],
  "rmssd_ms": 41.5,
  "contact": true
}
```

| Field | Type | Notes |
| :--- | :--- | :--- |
| `hr_bpm` | float | Heart rate. |
| `rr_ms` | int[] \| null | Beat intervals received since the last message (RR or PPI). `null` if the device does not provide them. |
| `rmssd_ms` | float \| null | RMSSD over the reader's rolling window (state the window length in the module README). `null` until enough clean intervals. |
| `contact` | bool \| null | Skin contact flag if the device reports it. |

### `telemetry/eeg`

```json
"data": {
  "bands": { "delta": 3.1, "theta": 6.2, "alpha": 11.4, "beta": 15.0, "gamma": 4.2 },
  "alpha_left": 10.8,
  "alpha_right": 12.0,
  "faa": 0.105,
  "calm_score": 0.42,
  "focus_score": 0.61
}
```

| Field | Type | Notes |
| :--- | :--- | :--- |
| `bands` | object | Average power per band across channels. |
| `alpha_left` / `alpha_right` | float | Alpha power on the left/right frontal channels (Crown: F5 / F6). |
| `faa` | float | Frontal alpha asymmetry, `ln(alpha_right) - ln(alpha_left)`. Positive = approach / higher valence. |
| `calm_score` / `focus_score` | float \| null | Neurosity's own scores (0–1) if used. |

### `telemetry/cv`

```json
"data": {
  "face_detected": true,
  "valence": 0.35,
  "arousal": 0.10,
  "emotion": "happy",
  "emotion_confidence": 0.82,
  "gaze_on_target": null
}
```

| Field | Type | Notes |
| :--- | :--- | :--- |
| `face_detected` | bool | If `false`, publish with `status: "no_signal"` and `data: null` instead. |
| `valence` / `arousal` | float | Circumplex estimate, both in −1…1. |
| `emotion` | string | `happy`, `neutral`, `surprise`, `sad`, `fear`, `angry`, `disgust`. |
| `emotion_confidence` | float | 0–1. |
| `gaze_on_target` | bool \| null | Eye contact with the robot/camera. `null` until implemented. |

## Hub outputs

### `affect/state` (hub → app, recorder)

`source: "hub"`. Published at a fixed 1 Hz, not once per incoming message.

```json
"data": {
  "session_id": "2026-10-28T14-05-00_ab12",
  "valence": -0.30,
  "arousal": 0.55,
  "stress": 0.68,
  "label": "stressed",
  "sources": { "polar": "ok", "eeg": "stale", "cv": "ok" }
}
```

| Field | Type | Notes |
| :--- | :--- | :--- |
| `session_id` | string \| null | `null` when no session is running. |
| `valence` / `arousal` | float \| null | Fused, −1…1. `null` if no source is fresh. |
| `stress` | float \| null | 0–1. |
| `label` | string | `"calm"`, `"neutral"`, `"stressed"`, `"unknown"`. |
| `sources` | object | Per source: `"ok"`, `"stale"` (no message for > 3 s), `"no_signal"`, `"missing"` (never seen). |

### `robot/command` (hub → NAO bridge)

`source: "hub"`. The robot only executes; all interview and adaptation logic lives in the hub, so swapping NAO for another robot only means rewriting the bridge. The hub sends adaptive commands only during the `interview` phase of an `adaptive` session, or when no session exists at all (so the loop can be demoed before the app exists).

```json
"data": {
  "command_id": "c-0042",
  "action": "say",
  "text": "Take your time. Can you tell me about a project you are proud of?",
  "animation": "explain",
  "speech_rate": 0.9
}
```

| Field | Type | Notes |
| :--- | :--- | :--- |
| `command_id` | string | Echoed back in `robot/status`. |
| `action` | string | `"say"`, `"gesture"`, `"stop"`. |
| `text` | string \| null | For `say`. |
| `animation` | string \| null | Abstract tag (`"explain"`, `"open_arms"`, `"nod"`, `"idle"`); the bridge maps it to robot-specific animations. |
| `speech_rate` | float \| null | 1.0 = normal; < 1 slower. |

### `robot/status` (NAO bridge → hub, app) — retained

`source: "robot"`.

```json
"data": {
  "state": "speaking",
  "last_command_id": "c-0042",
  "robot": "nao_virtual"
}
```

`state`: `"offline"`, `"idle"`, `"speaking"`, `"moving"`. Set an MQTT last-will on this topic with `state: "offline"`.

## Session (app ↔ hub) — reserved for the app phase

### `session/control` (app → hub)

```json
"data": {
  "command": "start",
  "interview_type": "software_junior",
  "adaptive": true
}
```

`command`: `"start"`, `"stop"`, `"next_question"`. `adaptive` is the experimental condition (robot adapts vs. does not).

### `session/state` (hub → app, robot) — retained

```json
"data": {
  "session_id": "2026-10-28T14-05-00_ab12",
  "phase": "baseline",
  "interview_type": "software_junior",
  "adaptive": true,
  "question_index": 0,
  "question_count": 8
}
```

`phase`: `"idle"`, `"baseline"` (resting recording for per-user normalization), `"interview"`, `"finished"`.

## Mock mode

`hub-server/mock-simulator` publishes contract-valid telemetry with `"mock": true`, so the app and robot can be developed without hardware. It is opt-in per source (for example, mock only EEG while the real Polar and camera run). Consumers must not depend on `mock`; it exists so recordings can be filtered.
