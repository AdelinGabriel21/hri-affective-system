import json
import os
import time
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "hri.thecraceatree.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "443"))

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport="websockets")

if MQTT_PORT == 443:
    client.tls_set()

# Functii speciale de debug pentru a vedea ce se intampla cu conexiunea
def on_connect(client, userdata, flags, rc, properties=None):
    print(f"\n[DEBUG] Status conectare MQTT: {rc} (0 inseamna succes)")

def on_disconnect(client, userdata, disconnect_flags, rc, properties=None):
    print(f"\n[DEBUG] Deconectat de la MQTT. Cod eroare: {rc}")

client.on_connect = on_connect
client.on_disconnect = on_disconnect

print(f"[TEST] Se incearca conectarea la wss://{MQTT_HOST}:{MQTT_PORT}...")
client.connect(MQTT_HOST, MQTT_PORT, 60)
client.loop_start()

# Asteptam 2 secunde sa aiba timp sa faca handshake-ul securizat
time.sleep(2)

test_cmd = {
    "v": 1,
    "source": "hub",
    "device": "fusion_engine",
    "timestamp": round(time.time(), 3),
    "status": "ok",
    "mock": True,
    "data": {
        "command_id": "c-test-02",
        "action": "say",
        "text": "Salut! Acum ar trebui să meargă.",
        "animation": "open_arms",
        "speech_rate": 0.9
    }
}

print("[TEST] Trimitere comanda...")
client.publish("robot/command", json.dumps(test_cmd), qos=0)
print("[TEST] Comanda expediata in eter!")

time.sleep(2)
client.loop_stop()
client.disconnect()