# Demo runbook: initial presentation

How to bring the whole system up, rehearse it, and present it. Every command here was tested on 17 Nov 2026.

## Who runs what

| Person | Laptop runs | Folder |
| :--- | :--- | :--- |
| Adelin | Polar reader, hub monitor, session control, mock generator. **Goes on the projector.** | `sensory-inputs/polar-strap`, `hub-server/tools`, `hub-server/mock-simulator` |
| Alisa | Choregraphe + robot bridge (on the projector laptop, so the robot is visible) | `robot-nao` |
| Andreea | Computer vision (webcam window) | `sensory-inputs/computer-vision` |
| Diana | Neurosity Crown reader | `sensory-inputs/eeg-crown` |
| Server | Mosquitto + fusion engine, always on | `hub-server/fusion-engine` |


## Three rules

1. **Only one fusion engine at a time.** It runs on the server. Never start a second one on a laptop while the server's is running.
2. **Never mock a sensor that is running for real.** The hub would average real and fake data.
3. **Start a session before the mock scenario.** The session resets the baseline; see part 2.

---

## A. Once, before the rehearsal (Adelin)

### A1. Fusion engine on the server

The server needs the latest `main`, which includes `install_service.sh`. On the server (the LXC with Mosquitto):

```bash
sudo apt install -y git
sudo git clone https://github.com/AdelinGabriel21/hri-affective-system.git /opt/hri-affective-system
sudo bash /opt/hri-affective-system/hub-server/fusion-engine/install_service.sh
```

Expected ending: `[SUCCESS] hri-fusion-engine is running` and `Connected to broker (Success)`.

It now starts with the server and restarts itself after a crash. Useful commands:

| What | Command |
| :--- | :--- |
| Is it running? | `systemctl status hri-fusion-engine` |
| Live logs | `journalctl -u hri-fusion-engine -f` |
| Update after a merge | `cd /opt/hri-affective-system && sudo git pull && sudo bash hub-server/fusion-engine/install_service.sh` |
| Restart (also resets the baseline) | `sudo systemctl restart hri-fusion-engine` |

**Check:** in MQTT Explorer, `affect/state` updates once per second, and `session/state` shows `"phase": "idle"`.

**If the server is not reachable,** run the fusion engine on Adelin's laptop instead. Use it only while the server's is off (rule 1). In `hub-server/fusion-engine`, create `.env`:

```ini
MQTT_HOST="hri.thecraceatree.com"
MQTT_PORT=443
MQTT_USE_WSS=true
BASELINE_SECONDS=60
```

Then run it with the Polar venv, which already has the packages:

```bash
..\..\sensory-inputs\polar-strap\venv\Scripts\python fusion_engine.py
```

### A2. Mock generator `.env` (Adelin's laptop)

In `hub-server/mock-simulator`:

```bash
copy .env.example .env
```

The tools in `hub-server/tools` need no setup: their default is the public broker. Run them with the Polar venv, which has the two packages they need (`paho-mqtt`, `python-dotenv`).

### A3. Each person, on their own laptop

```bash
git pull
```

Run the setup script of your module once if you haven't: `setup.bat` in your folder. In `robot-nao` it also configures WSL.

---

## B. Rehearsal (everyone, about 20 minutes)

Screen-record the whole rehearsal. It's the backup if the live demo fails.

### B1. Start the modules (any order)

| Who | Command (in the module folder) | It works when |
| :--- | :--- | :--- |
| Adelin | `venv\Scripts\python polar_reader.py` | `HR .. bpm (ppi)` lines after about 25 s, RMSSD a number after about 1 minute |
| Andreea | `venv\Scripts\python vision_affect.py` | The webcam window shows the emotion and valence/arousal; `NO FACE` when you leave |
| Diana | `.venv\Scripts\python eeg_reader.py` | `FAA: .. Calm: .. Focus: ..` lines. An empty `NEUROSITY_DEVICE_ID` in `.env` runs it in mock mode |
| Alisa | Open Choregraphe and wait for the 3D robot, then `.\run.bat` | `Conectat cu succes la NAOqi!`, then `Conectat la broker` |

### B2. Check the live data (hub monitor, Adelin)

In `hub-server/tools`, start the monitor and leave it running:

```bash
..\..\sensory-inputs\polar-strap\venv\Scripts\python hub_monitor.py
```

Every second it prints two lines: what each sensor sends, and what the fusion engine concludes. Check:

- [ ] the first line shows values for `POLAR`, `EEG` and `CV`
- [ ] the `HUB ->` line ends with `[polar:ok eeg:ok cv:ok]`
- [ ] `ROBOT status: idle` appeared when the monitor started
- [ ] Take the Polar off: `POLAR` changes to `no data` within about 10 s

MQTT Explorer (`hri.thecraceatree.com`, port `443`, `ws://`, encryption on, basepath `mqtt`) still works for looking at the raw messages.

Robot check: `.\run.bat send` in a second terminal in `robot-nao`. The robot opens its arms, and the text appears in Choregraphe under View → Dialog.

### B3. Part 2: the robot reacts on its own (mock scenario)

Keep the robot bridge and the hub monitor running; put the monitor next to Choregraphe on the projector.

1. **Stop** Polar, CV and EEG with Ctrl+C (rule 2).
2. **Start a session** (Adelin, in `hub-server/tools`). The monitor shows `SESSION phase: baseline`:

   ```bash
   ..\..\sensory-inputs\polar-strap\venv\Scripts\python session_control.py start
   ```

3. **Immediately** start the mock (Adelin, in `hub-server/mock-simulator`):

   ```bash
   ..\..\sensory-inputs\polar-strap\venv\Scripts\python mock_generator.py
   ```

4. Expected, as tested on 18 Nov: 60 s of `CALM`/`NEUTRAL` while the baseline is recorded. Then `SESSION phase: interview`, the stress episode starts, and stress climbs 0.28 → 0.62 → `STRESSED` within about 5 s. Three seconds later: `HUB -> ROBOT: say [open_arms]`, and the robot opens its arms and says *"Take your time. Let's slow down and take a deep breath before we continue."* That's about 70–75 s after the session started. If you miss it, the next stress episode comes 2 minutes later.
5. **Stop:** Ctrl+C on the mock, then:

   ```bash
   ..\..\sensory-inputs\polar-strap\venv\Scripts\python session_control.py stop
   ```

For the research comparison later, `session_control.py start --non-adaptive` starts the same session with a robot that does not react.

---

## C. The presentation (about 10 minutes)

| Time | What | Who |
| :--- | :--- | :--- |
| 0–2 min | The idea: interview trainer, NAO adapts to stress from EEG + heart + face. Architecture slide: sensors → MQTT broker → fusion engine → robot (app later). One shared message contract, which is why four people could build independently. | Adelin |
| 2–5 min | **Live part 1:** each module running, and the hub monitor showing all three sensors arriving. Polar on the arm: HR + RMSSD, then take it off and it reports `no_signal`, because optical sensors produce fake beats off the skin. Camera: emotion + valence/arousal. Crown: FAA, calm, focus. | Adelin, Andreea, Diana |
| 5–8 min | **Live part 2:** start a session, run the scripted stress scenario, and the robot reacts by itself. While the baseline is recorded, explain it: the hub learns this person's resting heart rate and calm score, because 80 bpm is calm for one person and stressful for another. Then point at the monitor as stress climbs to `STRESSED`, and at the robot a few seconds later. Say clearly that this part uses simulated sensor data. | Alisa, Adelin |
| 8–10 min | Next steps: question bank + interview flow, session recorder and final report, mobile app, physical NAO. Research question: does an adaptive robot help people calm down? (Adaptive vs non-adaptive sessions, already in the session message as `"adaptive"`.) | Everyone |

Before you start: Choregraphe open with the robot visible, the bridge running, the hub monitor running, the Polar worn for at least a minute (baseline), and the recording from last night ready.

---

## D. If something goes wrong

| Symptom | Fix |
| :--- | :--- |
| Polar: `No Bluetooth devices seen at all` | Win + A → Bluetooth off, wait 5 s, on. |
| Polar: `Not found among N nearby devices` | Close Polar Flow/Beat on your phone; check that the armband is on. |
| Choregraphe stuck on "loading 3D modules" | Task Manager: end every `choregraphe-bin`, `choregraphe_launcher`, `naoqi-bin`, `naoqi-service`, then reopen. |
| Robot: `virtual robot is not running` | Choregraphe isn't open yet, or is still loading. |
| Robot: `Future canceled` | You ran Python directly; use `.\run.bat`. |
| Robot looks like it fell over in the 3D view | A display glitch only (no physics). Click Stand in the posture library. |
| The monitor prints no `HUB ->` lines | The fusion engine isn't running. On the server: `systemctl status hri-fusion-engine`, `journalctl -u hri-fusion-engine -f`. |
| A source shows `stale` in the `HUB ->` line | That module stopped publishing: check its terminal. |
| The robot doesn't react in part 2 | Check that the monitor showed `SESSION phase: interview` and `ROBOT status: idle`; wait for the next stress episode (2 min). |
| University Wi-Fi blocks something | Everything uses port 443 (WSS); fall back to a phone hotspot. The Crown also needs internet. |
