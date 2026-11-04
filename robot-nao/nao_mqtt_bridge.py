import json
import os
import sys
import threading
import time
import paho.mqtt.client as mqtt
import qi
from dotenv import load_dotenv
import qi
s = qi.Session()
s.connect("tcp://127.0.0.1:PORT")
print(s.services())

load_dotenv()

# Configuratie NAO
NAO_IP = os.getenv("NAO_IP", "127.0.0.1")
NAO_PORT = int(os.getenv("NAO_PORT", "9559"))
ROBOT_NAME = os.getenv("NAO_DEVICE_NAME", "nao_virtual")

# Configuratie MQTT
MQTT_HOST = os.getenv("MQTT_HOST", "hri.thecraceatree.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "443"))

TOPIC_COMMAND = "robot/command"
TOPIC_STATUS = "robot/status"
TOPIC_SESSION = "session/state"


def build_status_envelope(state_str, last_cmd_id, envelope_status="ok"):
    """Construieste mesajul conform contractului v1 pentru robot/status."""
    return {
        "v": 1,
        "source": "robot",
        "device": ROBOT_NAME,
        "timestamp": round(time.time(), 3),
        "status": envelope_status,
        "mock": False,
        "data": {
            "state": state_str,
            "last_command_id": last_cmd_id,
            "robot": ROBOT_NAME
        }
    }


class NaoBridge:
    def __init__(self, ip, port, mqtt_client):
        self.mqtt_client = mqtt_client
        self.last_command_id = None
        self.current_state = "offline"
        self.lock = threading.Lock()
        self.abort_flag = threading.Event()

        connection_url = f"tcp://{ip}:{port}"
        print(f"[NAO] Conectare la {connection_url} ({ROBOT_NAME})...")

        self.session = qi.Session()
        try:
            self.session.connect(connection_url)
            print("[NAO] Conectat cu succes la NAOqi!")
        except RuntimeError as e:
            print(f"[EROARE] Conectarea la NAO a esuat: {e}")
            sys.exit(1)

        self.motion = self.session.service("ALMotion")
        self.posture = self.session.service("ALRobotPosture")
        self.tts = self.session.service("ALTextToSpeech")
        try:
            self.leds = self.session.service("ALLeds")
        except Exception:
            self.leds = None

        self.motion.wakeUp()
        self.posture.goToPosture("StandInit", 0.5)
        self.set_eyes_color("white")
        self.publish_status("idle")

    def set_eyes_color(self, color_name):
        if not self.leds:
            return
        colors = {
            "white": 0xFFFFFF,
            "blue": 0x3399FF,
            "green": 0x33CC33,
            "cyan": 0x00FFFF
        }
        hex_val = colors.get(color_name, 0xFFFFFF)
        try:
            self.leds.fadeRGB("FaceLeds", hex_val, 0.3)
        except Exception:
            pass

    def publish_status(self, state_str):
        self.current_state = state_str
        payload = build_status_envelope(state_str, self.last_command_id, envelope_status="ok")
        self.mqtt_client.publish(TOPIC_STATUS, json.dumps(payload), qos=1, retain=True)
        print(f"[STATUS] state='{state_str}' | last_command_id='{self.last_command_id}'")

    def execute_animation(self, animation_tag):
        if not animation_tag or animation_tag == "idle":
            self.posture.goToPosture("StandInit", 0.4)
            return

        print(f"[ANIM] Rulare animatie: {animation_tag}")

        if animation_tag == "open_arms":
            self.set_eyes_color("blue")
            names = [
                "LShoulderPitch", "LShoulderRoll", "LElbowRoll", "LWristYaw",
                "RShoulderPitch", "RShoulderRoll", "RElbowRoll", "RWristYaw",
                "HeadPitch"
            ]
            angles = [0.7, 0.5, -0.4, -1.0, 0.7, -0.5, 0.4, 1.0, 0.15]
            self.motion.setAngles(names, angles, 0.15)
            self._sleep_interruptible(2.5)
            if not self.abort_flag.is_set():
                self.posture.goToPosture("StandInit", 0.3)
            self.set_eyes_color("white")

        elif animation_tag == "nod":
            self.set_eyes_color("green")
            for _ in range(2):
                if self.abort_flag.is_set():
                    break
                self.motion.setAngles("HeadPitch", 0.28, 0.25)
                self._sleep_interruptible(0.35)
                self.motion.setAngles("HeadPitch", -0.08, 0.25)
                self._sleep_interruptible(0.35)
            self.motion.setAngles("HeadPitch", 0.0, 0.2)
            self.set_eyes_color("white")

        elif animation_tag == "explain":
            self.set_eyes_color("cyan")
            names = ["RShoulderPitch", "RShoulderRoll", "RElbowYaw", "RElbowRoll", "RWristYaw"]
            angles = [0.6, -0.2, 1.2, 0.9, 0.6]
            self.motion.setAngles(names, angles, 0.2)
            self._sleep_interruptible(1.8)
            if not self.abort_flag.is_set():
                self.posture.goToPosture("StandInit", 0.3)
            self.set_eyes_color("white")

        else:
            self.posture.goToPosture("StandInit", 0.4)

    def _sleep_interruptible(self, duration):
        step = 0.05
        elapsed = 0.0
        while elapsed < duration and not self.abort_flag.is_set():
            time.sleep(step)
            elapsed += step

    def stop_all_actions(self):
        self.abort_flag.set()
        try:
            self.tts.stopAll()
        except Exception:
            pass
        self.motion.stopMove()
        self.posture.goToPosture("StandInit", 0.5)
        self.set_eyes_color("white")
        self.publish_status("idle")

    def handle_command(self, envelope):
        if envelope.get("status") != "ok" or not envelope.get("data"):
            return

        data = envelope["data"]
        cmd_id = data.get("command_id")
        action = data.get("action")
        text = data.get("text")
        animation = data.get("animation")
        speech_rate = data.get("speech_rate")

        self.last_command_id = cmd_id

        if action == "stop":
            print(f"[CMD {cmd_id}] STOP primit!")
            self.stop_all_actions()
            return

        with self.lock:
            self.abort_flag.clear()

            if action == "say":
                self.publish_status("speaking")
                rate = speech_rate if speech_rate is not None else 1.0
                nao_speed = int(max(50, min(150, rate * 100)))
                try:
                    self.tts.setParameter("speed", nao_speed)
                except Exception:
                    pass

                anim_thread = None
                if animation and animation != "idle":
                    anim_thread = threading.Thread(
                        target=self.execute_animation, args=(animation,), daemon=True
                    )
                    anim_thread.start()

                if text and not self.abort_flag.is_set():
                    print(f"[CMD {cmd_id}] SAY (rate={rate}): \"{text}\"")
                    self.tts.say(str(text))

                if anim_thread and anim_thread.is_alive():
                    anim_thread.join(timeout=3.0)

                if not self.abort_flag.is_set():
                    self.publish_status("idle")

            elif action == "gesture":
                self.publish_status("moving")
                print(f"[CMD {cmd_id}] GESTURE: {animation}")
                self.execute_animation(animation)
                if not self.abort_flag.is_set():
                    self.publish_status("idle")

    def handle_session_state(self, envelope):
        data = envelope.get("data")
        if not data:
            return
        phase = data.get("phase")
        print(f"[SESSION] Faza curenta: {phase}")
        if phase in ("idle", "finished"):
            self.stop_all_actions()

    def shutdown(self):
        print("\n[SAFETY] Asezare robot in Crouch si eliberare motoare (rest)...")
        self.abort_flag.set()
        try:
            self.publish_status("offline")
            self.posture.goToPosture("Crouch", 0.5)
            self.motion.rest()
        except Exception as e:
            print(f"[EROARE SHUTDOWN]: {e}")




def main():
    client_id = f"nao-bridge-{int(time.time())}"
    mqtt_client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, transport="websockets"
    )

    if MQTT_PORT == 443:
        mqtt_client.tls_set()

    lwt_payload = json.dumps(build_status_envelope("offline", None, envelope_status="ok"))
    mqtt_client.will_set(TOPIC_STATUS, payload=lwt_payload, qos=1, retain=True)

    # 1. Definim ce se intampla cand ne conectam
    def on_connect(client, userdata, flags, rc, properties=None):
        print(f"[MQTT] Conectat la broker ({rc}). Abonare la '{TOPIC_COMMAND}' si '{TOPIC_SESSION}'...")
        client.subscribe([(TOPIC_COMMAND, 1), (TOPIC_SESSION, 1)])
        bridge.publish_status(bridge.current_state)

    def on_message(client, userdata, msg):
        try:
            envelope = json.loads(msg.payload.decode("utf-8"))
            if envelope.get("v") != 1:
                return
            if msg.topic == TOPIC_COMMAND:
                threading.Thread(
                    target=bridge.handle_command, args=(envelope,), daemon=True
                ).start()
            elif msg.topic == TOPIC_SESSION:
                bridge.handle_session_state(envelope)
        except Exception as e:
            print(f"[EROARE MESAJ MQTT]: {e}")

    # 2. Atasam functiile INAINTE sa ne conectam efectiv!
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    # 3. Ne conectam la robot
    bridge = NaoBridge(NAO_IP, NAO_PORT, mqtt_client)

    # 4. Ne conectam la MQTT
    print(f"[MQTT] Conectare la wss://{MQTT_HOST}:{MQTT_PORT}...")
    mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

    try:
        mqtt_client.loop_forever()
    except KeyboardInterrupt:
        print("\n[STOP] Oprire manuala de la tastatura.")
    finally:
        bridge.shutdown()
        mqtt_client.disconnect()


if __name__ == "__main__":
    main()