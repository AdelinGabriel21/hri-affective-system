"""Polar Verity Sense reader (contract v1, see docs/protocol_spec.md).

Connects over Bluetooth LE, reads heart rate from the standard Heart Rate Service
and publishes telemetry/polar once per second.

    python polar_reader.py --scan       # list nearby Polar devices and their addresses
    python polar_reader.py --dry-run    # connect and print, without MQTT
    python polar_reader.py              # connect and publish to the broker
"""
import argparse
import asyncio
import json
import os
import re
import time
from collections import deque

import paho.mqtt.client as mqtt
from bleak import BleakClient, BleakScanner
from dotenv import load_dotenv

load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "hri.thecraceatree.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", 443))
USE_WSS = os.getenv("MQTT_USE_WSS", "true").lower() == "true"
MQTT_USERNAME = os.getenv("MQTT_USERNAME") or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD") or None
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "telemetry/polar")
DEVICE_ADDRESS = os.getenv("POLAR_DEVICE_ADDRESS", "").strip()

CONTRACT_VERSION = 1
PUBLISH_SECONDS = 1.0
NO_DATA_SECONDS = 3.0          # no HR notification for this long -> "no_signal"
RECONNECT_SECONDS = 5.0
SCAN_SECONDS = 10.0
RMSSD_WINDOW_SECONDS = 60      # beat intervals kept for RMSSD

# BLE address: a MAC on Windows/Linux (A0:9E:1A:A6:FC:0B), a UUID on macOS
ADDRESS_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$|^[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$")

# Standard Bluetooth SIG characteristics
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"


def parse_hr_measurement(data):
    """Decode a Heart Rate Measurement notification (characteristic 0x2A37).

    Byte 0 is a flags field:
      bit 0    heart rate value is uint16 (otherwise uint8)
      bit 2    sensor contact feature supported; if set, bit 1 = contact detected
      bit 3    energy expended field present (uint16, skipped)
      bit 4    RR intervals present (uint16 each, in units of 1/1024 s)
    All multi-byte values are little-endian.
    """
    flags = data[0]
    i = 1
    if flags & 0x01:
        hr = int.from_bytes(data[i:i + 2], "little")
        i += 2
    else:
        hr = data[i]
        i += 1
    contact = bool(flags & 0x02) if flags & 0x04 else None
    if flags & 0x08:
        i += 2
    rr_ms = []
    if flags & 0x10:
        while i + 1 < len(data):
            rr_ms.append(round(int.from_bytes(data[i:i + 2], "little") * 1000 / 1024))
            i += 2
    return {"hr_bpm": hr, "rr_ms": rr_ms, "contact": contact}


def compute_rmssd(rr_window):
    """RMSSD in ms over the recent beat intervals, or None without enough clean ones.

    TODO(Adelin): implement. Suggested steps:
      1. drop impossible intervals (e.g. outside 300-2000 ms)
      2. drop intervals that differ from the previous one by more than ~20% (artifacts)
      3. return None if fewer than ~20 intervals are left
      4. RMSSD = sqrt(mean(diff(rr) ** 2))
    If the device sends no RR intervals, rr_window stays empty and this returns None,
    which the hub handles (it then uses heart rate only).
    """
    return None


class PolarState:
    """Latest values from the sensor, shared by the BLE callback and the publish loop."""

    def __init__(self):
        self.connected = False
        self.device_name = None
        self.hr = None
        self.contact = None
        self.last_notification = 0.0
        self.rr_supported = False   # becomes True after the first RR interval arrives
        self.pending_rr = []        # intervals since the last publish
        self.rr_window = deque()    # (arrival time, rr_ms) for RMSSD

    def on_notification(self, _sender, data):
        try:
            parsed = parse_hr_measurement(data)
        except IndexError:
            print(f"[POLAR] Ignoring malformed notification: {bytes(data).hex()}")
            return
        now = time.time()
        self.hr = parsed["hr_bpm"]
        self.contact = parsed["contact"]
        self.last_notification = now
        if parsed["rr_ms"]:
            self.rr_supported = True
            self.pending_rr.extend(parsed["rr_ms"])
            self.rr_window.extend((now, rr) for rr in parsed["rr_ms"])
        while self.rr_window and now - self.rr_window[0][0] > RMSSD_WINDOW_SECONDS:
            self.rr_window.popleft()

    def take_packet(self, now):
        """Return (status, data) for the next message and reset the per-message RR buffer."""
        rr, self.pending_rr = self.pending_rr, []
        if (not self.connected or now - self.last_notification > NO_DATA_SECONDS
                or not self.hr or self.contact is False):
            return "no_signal", None
        return "ok", {
            "hr_bpm": float(self.hr),
            "rr_ms": rr if self.rr_supported else None,
            "rmssd_ms": compute_rmssd([rr_ms for _, rr_ms in self.rr_window]),
            "contact": self.contact,
        }


def envelope(status, data):
    return {
        "v": CONTRACT_VERSION,
        "source": "polar",
        "device": "polar_verity_sense",
        "timestamp": time.time(),
        "status": status,
        "mock": False,
        "data": data,
    }


def is_polar(device, adv=None):
    name = device.name or (adv.local_name if adv else None) or ""
    return name.startswith("Polar")


async def scan():
    print(f"[POLAR] Scanning for {SCAN_SECONDS:.0f} s...")
    found = await BleakScanner.discover(timeout=SCAN_SECONDS, return_adv=True)
    polar = sorted(((d, adv) for d, adv in found.values() if is_polar(d, adv)), key=lambda x: -x[1].rssi)
    if not polar:
        print("[POLAR] No Polar device found. Is it switched on, worn, and disconnected from the phone app?")
        return
    for device, adv in polar:
        print(f"  {device.address}  {device.name or adv.local_name}  RSSI {adv.rssi} dBm")
    print("[POLAR] To always connect to the closest one, put this line in .env (address only, no name):")
    print(f'  POLAR_DEVICE_ADDRESS="{polar[0][0].address}"')


async def find_polar():
    if DEVICE_ADDRESS:
        return await BleakScanner.find_device_by_address(DEVICE_ADDRESS, timeout=SCAN_SECONDS)
    return await BleakScanner.find_device_by_filter(is_polar, timeout=SCAN_SECONDS)


async def sensor_loop(state):
    """Connect, subscribe to heart rate notifications and reconnect whenever the link drops."""
    loop = asyncio.get_running_loop()
    while True:
        target = DEVICE_ADDRESS or "any Polar device"
        print(f"[POLAR] Looking for {target}...")
        device = await find_polar()
        if device is None:
            print(f"[POLAR] Not found. Retrying in {RECONNECT_SECONDS:.0f} s...")
            await asyncio.sleep(RECONNECT_SECONDS)
            continue

        disconnected = asyncio.Event()
        try:
            # Windows may serve a stale/partial GATT table from its cache right after a
            # reconnect ("characteristic not found"), so always read it from the device.
            async with BleakClient(
                device,
                disconnected_callback=lambda _: loop.call_soon_threadsafe(disconnected.set),
                winrt={"use_cached_services": False},
            ) as client:
                # The name is only in some advertisements, so keep the last one seen
                state.device_name = device.name or state.device_name or device.address
                print(f"[POLAR] Connected to {state.device_name} ({device.address})")
                if client.services.get_characteristic(HR_MEASUREMENT_UUID) is None:
                    raise RuntimeError("heart rate characteristic missing from the service table")
                try:
                    battery = await client.read_gatt_char(BATTERY_LEVEL_UUID)
                    print(f"[POLAR] Battery: {battery[0]}%")
                except Exception:
                    pass  # battery is nice to have, not required
                await client.start_notify(HR_MEASUREMENT_UUID, state.on_notification)
                state.connected = True
                await disconnected.wait()
        except Exception as e:
            print(f"[POLAR] Connection error: {e}")
        finally:
            state.connected = False
        print(f"[POLAR] Disconnected. Reconnecting in {RECONNECT_SECONDS:.0f} s...")
        await asyncio.sleep(RECONNECT_SECONDS)


async def publish_loop(state, mqtt_client):
    previous_status = None
    while True:
        status, data = state.take_packet(time.time())
        if mqtt_client:
            mqtt_client.publish(MQTT_TOPIC, json.dumps(envelope(status, data)))
        if data:
            rr = "n/a" if data["rr_ms"] is None else len(data["rr_ms"])
            print(f"[POLAR] HR {data['hr_bpm']:.0f} bpm | RR intervals {rr} | RMSSD {data['rmssd_ms']} | contact {data['contact']}")
        elif status != previous_status:
            # Still published every second, only printed when it starts
            print(f"[POLAR] {status} (connected={state.connected})")
        previous_status = status
        await asyncio.sleep(PUBLISH_SECONDS)


def create_mqtt_client():
    if USE_WSS:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="polar-reader", transport="websockets")
        client.tls_set()
    else:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="polar-reader")
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect = lambda c, u, f, rc, p=None: print(f"[MQTT] Connected to broker ({rc})")
    client.on_disconnect = lambda c, u, f, rc, p=None: (
        print(f"[MQTT] Connection lost ({rc}), retrying...") if rc.is_failure else None)
    scheme = "wss" if USE_WSS else "tcp"
    print(f"[MQTT] Connecting to {scheme}://{MQTT_HOST}:{MQTT_PORT}...")
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)  # keeps retrying in the background
    client.loop_start()
    return client


async def run(dry_run):
    mqtt_client = None if dry_run else create_mqtt_client()
    state = PolarState()
    try:
        await asyncio.gather(sensor_loop(state), publish_loop(state, mqtt_client))
    finally:
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Polar Verity Sense -> MQTT telemetry/polar")
    parser.add_argument("--scan", action="store_true", help="list nearby Polar devices and exit")
    parser.add_argument("--dry-run", action="store_true", help="print readings without publishing to MQTT")
    args = parser.parse_args()
    if DEVICE_ADDRESS and not ADDRESS_PATTERN.match(DEVICE_ADDRESS):
        raise SystemExit(
            f'[POLAR] POLAR_DEVICE_ADDRESS="{DEVICE_ADDRESS}" is not a BLE address.\n'
            '        Use only the address from --scan, e.g. POLAR_DEVICE_ADDRESS="A0:9E:1A:A6:FC:0B", or leave it empty.')
    try:
        asyncio.run(scan() if args.scan else run(args.dry_run))
    except KeyboardInterrupt:
        print("[POLAR] Stopped.")


if __name__ == "__main__":
    main()
