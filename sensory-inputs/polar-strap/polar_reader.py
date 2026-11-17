"""Polar Verity Sense reader (contract v1, see docs/protocol_spec.md).

Connects over Bluetooth LE, reads heart rate from the standard Heart Rate Service,
streams beat-to-beat intervals (PPI) from Polar's Measurement Data (PMD) service
and publishes telemetry/polar once per second.

    python polar_reader.py --scan       # list nearby Polar devices and their addresses
    python polar_reader.py --dry-run    # connect and print, without MQTT
    python polar_reader.py              # connect and publish to the broker
"""
import argparse
import asyncio
import json
import math
import os
import re
import statistics
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
NO_DATA_SECONDS = 12.0         # no HR notification for this long -> "no_signal"
                               # (once PPI is requested the HR service updates only every ~5 s,
                               # with occasional gaps of 7+ s; removal is caught sooner by PPI checks)
RECONNECT_SECONDS = 5.0
SCAN_SECONDS = 10.0
RMSSD_WINDOW_SECONDS = 60      # beat intervals kept for RMSSD
PPI_TIMEOUT_SECONDS = 10.0     # PPI was streaming but stopped -> treat as not worn

# Artifact rejection for RMSSD
MAX_PPI_ERROR_MS = 30          # sensor's own error estimate above this -> beat rejected
                               # (measured median: 5-9 ms worn and still, 38-51 ms off the arm)
MIN_RR_MS, MAX_RR_MS = 300, 2000   # 30-200 bpm
MAX_SUCCESSIVE_CHANGE = 0.20   # interval jumping >20% vs the previous one -> artifact
MAX_DEVIATION_FROM_MEDIAN = 0.35   # interval >35% away from the window median -> artifact
                               # (catches runs of missed beats, which the jump rule lets through)
MIN_RMSSD_DIFFS = 20           # fewer successive differences than this -> RMSSD is None

# Heart rate: while PPI streams, HR comes from clean beats (the HR service is coarse in PPI mode
# and drifts when the armband is off or just put back). If no beat of the last few seconds is
# clean, the HR service is used only if it agrees with the clean beats of the whole window.
HR_RECENT_SECONDS = 10.0       # beats used for the published HR (about two 5 s batches)
MAX_HR_DISAGREEMENT_BPM = 15

# Signal quality while PPI streams. Off the arm the sensor can keep producing plausible-looking
# beats with contact=True (light/noise), so the two HR signals are cross-checked instead.
MAX_PPI_VS_SENSOR_BPM = 20     # HR from beats vs the sensor's own HR (which is coarse in PPI mode)
MIN_RECENT_CLEAN_FRACTION = 0.5    # fewer clean beats than this in the last HR_RECENT_SECONDS -> poor signal

# BLE address: a MAC on Windows/Linux (A0:9E:1A:A6:FC:0B), a UUID on macOS
ADDRESS_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$|^[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$")

# Standard Bluetooth SIG characteristics
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

# Polar Measurement Data (PMD) service, from the official polar-ble-sdk (BlePMDClient.kt)
PMD_CONTROL_UUID = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA_UUID = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"
PMD_TYPE_PPI = 0x03
PMD_OP_START = 0x02
PMD_OP_STOP = 0x03
PMD_RESPONSE_CODE = 0xF0
PPI_SAMPLE_BYTES = 6


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


def parse_pmd_ppi(data):
    """Decode a PMD data notification carrying PPI samples; None if it is not a PPI frame.

    Frame (PmdDataFrame.kt): byte 0 measurement type (low 6 bits), bytes 1-8 timestamp,
    byte 9 frame type (bit 7 = compressed, PPI only uses uncompressed type 0), then samples.
    Each 6-byte sample (PpiData.kt), little-endian:
      byte 0     heart rate (bpm)
      bytes 1-2  peak-to-peak interval (ms)
      bytes 3-4  error estimate of that interval (ms)
      byte 5     flags: bit 0 blocker (beat unreliable, e.g. movement),
                        bit 1 skin contact, bit 2 skin contact supported
    """
    if len(data) < 10 or data[0] & 0x3F != PMD_TYPE_PPI or data[9] != 0:
        return None
    samples = []
    payload = data[10:]
    for i in range(0, len(payload) - PPI_SAMPLE_BYTES + 1, PPI_SAMPLE_BYTES):
        s = payload[i:i + PPI_SAMPLE_BYTES]
        flags = s[5]
        samples.append({
            "hr_bpm": s[0],
            "ppi_ms": int.from_bytes(s[1:3], "little"),
            "error_ms": int.from_bytes(s[3:5], "little"),
            "blocker": bool(flags & 0x01),
            "contact": bool(flags & 0x02) if flags & 0x04 else None,
        })
    return samples


def is_reliable(beat):
    """Checks that need only the beat itself: sensor flags and physiological range."""
    error = beat["error_ms"]
    return (not beat["blocker"]
            and beat.get("contact") is not False
            and (error is None or error <= MAX_PPI_ERROR_MS)
            and MIN_RR_MS <= beat["ppi_ms"] <= MAX_RR_MS)


def clean_beats(beats):
    """Artifact rejection; returns (indices of kept beats, successive differences in ms).

    beats: oldest first, each {"ppi_ms": int, "error_ms": int | None, "blocker": bool}
    (error_ms is None and blocker False for RR intervals from the standard HR service).

    A difference is only taken between two beats that were next to each other and both
    kept. Bridging a rejected beat would compare non-adjacent intervals and inflate RMSSD.
    """
    reliable = [beat["ppi_ms"] for beat in beats if is_reliable(beat)]
    if not reliable:
        return [], []
    median = statistics.median(reliable)
    kept = []
    diffs = []
    previous = None  # last kept interval, reset to None by every rejected beat
    for i, beat in enumerate(beats):
        rr = beat["ppi_ms"]
        outlier = abs(rr - median) > MAX_DEVIATION_FROM_MEDIAN * median
        jump = previous is not None and abs(rr - previous) > MAX_SUCCESSIVE_CHANGE * previous
        if not is_reliable(beat) or outlier or jump:
            previous = None
            continue
        if previous is not None:
            diffs.append(rr - previous)
        previous = rr
        kept.append(i)
    return kept, diffs


def compute_rmssd(beats):
    """RMSSD in ms (root mean square of successive differences), or None without enough clean data."""
    _, diffs = clean_beats(beats)
    if len(diffs) < MIN_RMSSD_DIFFS:
        return None
    return round(math.sqrt(sum(d * d for d in diffs) / len(diffs)), 1)


class PolarState:
    """Latest values from the sensor, shared by the BLE callbacks and the publish loop."""

    def __init__(self):
        self.connected = False
        self.device_name = None
        self.hr = None
        self.hr_contact = None
        self.hr_source = None       # "ppi" or "sensor": where the last published HR came from
        self.last_notification = 0.0
        self.pending_rr = []        # intervals since the last publish
        self.beats = deque()        # (arrival time, beat dict) for RMSSD
        self.rr_supported = False   # True once intervals arrive from either source
        self.reset_ppi()

    def reset_ppi(self):
        """Forget PPI state; called on every new connection."""
        self.ppi_active = False     # True once the first PPI sample arrives
        self.ppi_contact = None
        self.last_ppi = 0.0
        self.beats.clear()          # beats from before a reconnect are not adjacent to new ones

    def add_beats(self, beats, now):
        self.rr_supported = True
        if self.beats and now - self.beats[-1][0] > PPI_TIMEOUT_SECONDS:
            self.beats.clear()      # gap (armband off): don't diff across it

        for beat in beats:
            if not beat["blocker"]:
                self.pending_rr.append(beat["ppi_ms"])
            self.beats.append((now, beat))
        while self.beats and now - self.beats[0][0] > RMSSD_WINDOW_SECONDS:
            self.beats.popleft()

    def on_notification(self, _sender, data):
        try:
            parsed = parse_hr_measurement(data)
        except IndexError:
            print(f"[POLAR] Ignoring malformed notification: {bytes(data).hex()}")
            return
        now = time.time()
        self.hr = parsed["hr_bpm"]
        self.hr_contact = parsed["contact"]
        self.last_notification = now
        if parsed["rr_ms"]:
            self.add_beats([{"ppi_ms": rr, "error_ms": None, "blocker": False} for rr in parsed["rr_ms"]], now)

    def on_pmd_data(self, _sender, data):
        samples = parse_pmd_ppi(data)
        if not samples:
            return
        now = time.time()
        if not self.ppi_active:
            print("[POLAR] PPI stream started.")
        self.ppi_active = True
        self.last_ppi = now
        self.ppi_contact = samples[-1]["contact"]
        self.add_beats(samples, now)

    def on_pmd_control(self, _sender, data):
        # Response: [0xF0, echoed op code, measurement type, error code, ...]; 0 = success
        if len(data) >= 4 and data[0] == PMD_RESPONSE_CODE and data[1] == PMD_OP_START:
            if data[3] == 0:
                print("[POLAR] PPI requested; the first intervals usually take ~25 s. Keep still.")
            else:
                print(f"[POLAR] PPI start refused, PMD error code {data[3]} (raw {bytes(data).hex()})")

    @property
    def contact(self):
        return self.ppi_contact if self.ppi_active else self.hr_contact

    def recent_beats(self, now, seconds):
        """(number of PPI beats from the last `seconds`, intervals among them that survived cleaning).

        Cleaning runs over the whole window, so the median and jump rules have context
        even for the newest beats.
        """
        entries = list(self.beats)
        kept = set(clean_beats([beat for _, beat in entries])[0])
        recent = [i for i, (t, beat) in enumerate(entries)
                  if now - t <= seconds and beat["error_ms"] is not None]
        return len(recent), [entries[i][1]["ppi_ms"] for i in recent if i in kept]

    def clean_ppi_hr(self, now, seconds):
        """Heart rate (bpm) from the median clean PPI interval of the last `seconds`, or None."""
        _, intervals = self.recent_beats(now, seconds)
        return 60000 / statistics.median(intervals) if intervals else None

    def heart_rate(self, now):
        """Return (hr_bpm, source, None) for the value to publish, or (None, None, reason)."""
        if not self.connected:
            return None, None, "not connected"
        if self.contact is False:
            return None, None, "no skin contact"
        if self.ppi_active:
            # Off the skin the armband keeps sending HR; PPI is what stops or flags no contact
            if now - self.last_ppi > PPI_TIMEOUT_SECONDS:
                return None, None, "PPI stopped (armband off?)"
            # Taken off the arm, the sensor's own HR stopped while PPI kept sending fake beats
            if now - self.last_notification > NO_DATA_SECONDS or not self.hr:
                return None, None, "no heart rate data from the sensor"
            total, intervals = self.recent_beats(now, HR_RECENT_SECONDS)
            if total and len(intervals) / total < MIN_RECENT_CLEAN_FRACTION:
                return None, None, f"poor signal ({len(intervals)}/{total} recent beats clean)"
            if intervals:
                hr = 60000 / statistics.median(intervals)
                if abs(hr - self.hr) > MAX_PPI_VS_SENSOR_BPM:
                    return None, None, f"PPI HR {hr:.0f} disagrees with sensor HR {self.hr}"
                return hr, "ppi", None
        # Fallback: the HR service (before PPI starts, or when no recent beat was clean)
        if now - self.last_notification > NO_DATA_SECONDS or not self.hr:
            return None, None, "no heart rate data"
        if self.ppi_active:
            reference = self.clean_ppi_hr(now, RMSSD_WINDOW_SECONDS)
            if reference is not None and abs(self.hr - reference) > MAX_HR_DISAGREEMENT_BPM:
                return None, None, f"HR {self.hr} disagrees with clean PPI beats ({reference:.0f} bpm)"
        return float(self.hr), "sensor", None

    def take_packet(self, now):
        """Return (status, data, reason) for the next message and reset the per-message RR buffer."""
        rr, self.pending_rr = self.pending_rr, []
        hr, self.hr_source, reason = self.heart_rate(now)
        if reason:
            return "no_signal", None, reason
        return "ok", {
            "hr_bpm": round(hr, 1),
            "rr_ms": rr if self.rr_supported else None,
            "rmssd_ms": compute_rmssd([beat for _, beat in self.beats]),
            "contact": self.contact,
        }, None


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


STUCK_BLUETOOTH_HINT = ("[POLAR] No Bluetooth devices seen at all: the laptop's Bluetooth looks stuck.\n"
                        "        Turn Bluetooth off and on (Win+A), or restart the laptop if that doesn't help.")


async def scan():
    print(f"[POLAR] Scanning for {SCAN_SECONDS:.0f} s...")
    found = await BleakScanner.discover(timeout=SCAN_SECONDS, return_adv=True)
    polar = sorted(((d, adv) for d, adv in found.values() if is_polar(d, adv)), key=lambda x: -x[1].rssi)
    if not found:
        print(STUCK_BLUETOOTH_HINT)
        return
    if not polar:
        print(f"[POLAR] No Polar device among {len(found)} nearby devices. Is it switched on, worn,"
              " and not connected to a phone or another program?")
        return
    for device, adv in polar:
        print(f"  {device.address}  {device.name or adv.local_name}  RSSI {adv.rssi} dBm")
    print("[POLAR] To always connect to the closest one, put this line in .env (address only, no name):")
    print(f'  POLAR_DEVICE_ADDRESS="{polar[0][0].address}"')


async def find_polar():
    """Scan until the sensor shows up; returns (device or None, number of BLE devices seen in total).

    The total tells a stuck laptop Bluetooth (sees nothing at all) apart from a sensor
    that is off or busy (other devices are seen).
    """
    seen = set()
    match = {}
    found = asyncio.Event()

    def on_detect(device, adv):
        seen.add(device.address)
        wanted = (device.address.upper() == DEVICE_ADDRESS.upper()) if DEVICE_ADDRESS else is_polar(device, adv)
        if wanted and not found.is_set():
            match["device"] = device
            found.set()

    async with BleakScanner(detection_callback=on_detect):
        try:
            await asyncio.wait_for(found.wait(), SCAN_SECONDS)
        except asyncio.TimeoutError:
            pass
    return match.get("device"), len(seen)


async def start_ppi(client, state):
    """Subscribe to the PMD service and ask the sensor to stream PPI. HR keeps working if this fails."""
    if client.services.get_characteristic(PMD_CONTROL_UUID) is None:
        print("[POLAR] No PMD service on this device; continuing with heart rate only.")
        return
    try:
        # The SDK enables notifications on both characteristics before sending any command
        await client.start_notify(PMD_CONTROL_UUID, state.on_pmd_control)
        await client.start_notify(PMD_DATA_UUID, state.on_pmd_data)
        await client.write_gatt_char(PMD_CONTROL_UUID, bytes([PMD_OP_START, PMD_TYPE_PPI]), response=True)
    except Exception as e:
        print(f"[POLAR] Could not start PPI ({e}); continuing with heart rate only.")


async def stop_ppi(client):
    """Ask the sensor to stop PPI before disconnecting, so it is idle for the next connection."""
    try:
        await asyncio.wait_for(
            client.write_gatt_char(PMD_CONTROL_UUID, bytes([PMD_OP_STOP, PMD_TYPE_PPI]), response=True), 2.0)
    except Exception:
        pass  # best effort: the link may already be gone


async def sensor_loop(state):
    """Connect, subscribe to heart rate notifications and reconnect whenever the link drops."""
    loop = asyncio.get_running_loop()
    while True:
        target = DEVICE_ADDRESS or "any Polar device"
        print(f"[POLAR] Looking for {target}...")
        device, seen = await find_polar()
        if device is None:
            if seen == 0:
                print(STUCK_BLUETOOTH_HINT)
            else:
                print(f"[POLAR] Not found among {seen} nearby devices. Is it on, and not connected"
                      " to a phone or another program?")
            print(f"[POLAR] Retrying in {RECONNECT_SECONDS:.0f} s...")
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
                state.reset_ppi()
                await start_ppi(client, state)
                state.connected = True
                try:
                    await disconnected.wait()
                finally:
                    # Also runs on Ctrl+C: leave the sensor idle before the link is closed
                    if client.is_connected:
                        print("[POLAR] Stopping PPI and disconnecting...")
                        await stop_ppi(client)
        except Exception as e:
            print(f"[POLAR] Connection error: {e}")
        finally:
            state.connected = False
        print(f"[POLAR] Disconnected. Reconnecting in {RECONNECT_SECONDS:.0f} s...")
        await asyncio.sleep(RECONNECT_SECONDS)


async def publish_loop(state, mqtt_client):
    previous_reason = None
    while True:
        now = time.time()
        status, data, reason = state.take_packet(now)
        if mqtt_client:
            mqtt_client.publish(MQTT_TOPIC, json.dumps(envelope(status, data)))
        # Median error estimate of recent PPI beats, to tune MAX_PPI_ERROR_MS from real data
        errors = [beat["error_ms"] for t, beat in state.beats
                  if now - t <= HR_RECENT_SECONDS and beat["error_ms"] is not None]
        error = f"{statistics.median(errors):.0f} ms" if errors else "n/a"
        if data:
            rr = "n/a" if data["rr_ms"] is None else len(data["rr_ms"])
            # Share of beats in the RMSSD window that survived artifact rejection
            window = [beat for _, beat in state.beats]
            clean = f"{len(clean_beats(window)[0])}/{len(window)}" if window else "n/a"
            print(f"[POLAR] HR {data['hr_bpm']:.0f} bpm ({state.hr_source}) | RR intervals {rr} | clean beats {clean}"
                  f" | beat error {error} | RMSSD {data['rmssd_ms']} | contact {data['contact']}")
        elif reason != previous_reason:
            # Still published every second, only printed when the reason changes
            print(f"[POLAR] no_signal: {reason} | beat error {error}")
        previous_reason = reason
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
