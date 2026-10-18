"""MQTT gateway: Garmin heart-rate sensor -> AWS IoT Core.

Sources
  --source ble   Real data. Put the Garmin in "Broadcast Heart Rate" mode
                 (Bluetooth LE standard Heart Rate Service 0x180D). The PC
                 connects with bleak, subscribes to the Heart Rate
                 Measurement characteristic (0x2A37) and publishes every
                 notification over MQTT.
  --source sim   Simulated data with a rest -> walk -> run -> cool-down
                 session, for when the watch is not available.

Each reading goes to topic <topic_prefix>/<client_id>/heartrate using the
certificate Terraform wrote to ./certs and the endpoint in ./config.json.

Examples
  python gateway.py --source sim
  python gateway.py --source ble --scan            # list nearby HR sensors
  python gateway.py --source ble --name "Garmin"   # connect by name
  python gateway.py --source sim --dry-run         # no AWS, just print
"""
import argparse
import asyncio
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
HR_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"


# ---------------------------------------------------------------------------
# MQTT publisher
# ---------------------------------------------------------------------------
class Publisher:
    def __init__(self, config, dry_run):
        self.dry_run = dry_run
        self.topic = f"{config['topic_prefix']}/{config['client_id']}/heartrate"
        self.sent = 0
        if dry_run:
            return

        import paho.mqtt.client as mqtt

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=config["client_id"])
        self.client.tls_set(
            ca_certs=str(HERE / config["root_ca"]),
            certfile=str(HERE / config["certificate"]),
            keyfile=str(HERE / config["private_key"]),
        )
        self.client.on_connect = lambda c, u, f, rc, p: print(f"[mqtt] connected to {config['endpoint']} ({rc})")
        self.client.on_disconnect = lambda c, u, f, rc, p: print(f"[mqtt] disconnected ({rc})")
        self.client.connect(config["endpoint"], config.get("port", 8883), keepalive=60)
        self.client.loop_start()

    def publish(self, reading):
        payload = json.dumps(reading)
        if not self.dry_run:
            self.client.publish(self.topic, payload, qos=1)
        self.sent += 1
        rr = reading.get("rr_intervals_ms") or []
        print(f"{reading['timestamp']}  {reading['heart_rate_bpm']:>3} bpm  rr={rr}  -> {self.topic}")

    def close(self):
        if not self.dry_run:
            self.client.loop_stop()
            self.client.disconnect()
        print(f"\nPublished {self.sent} readings", file=sys.stderr)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# Real Garmin over Bluetooth LE
# ---------------------------------------------------------------------------
def parse_hr_measurement(data: bytearray):
    """Decode the standard BLE Heart Rate Measurement characteristic (0x2A37)."""
    flags = data[0]
    hr_uint16 = flags & 0x01
    contact_supported = flags & 0x04
    contact_detected = flags & 0x02
    energy_present = flags & 0x08
    rr_present = flags & 0x10

    i = 1
    if hr_uint16:
        bpm = int.from_bytes(data[i:i + 2], "little")
        i += 2
    else:
        bpm = data[i]
        i += 1

    if energy_present:
        i += 2  # skip Energy Expended (kJ)

    rr = []
    if rr_present:
        while i + 1 < len(data):
            raw = int.from_bytes(data[i:i + 2], "little")  # units of 1/1024 s
            rr.append(round(raw * 1000 / 1024))
            i += 2

    return {
        "timestamp": now_iso(),
        "heart_rate_bpm": bpm,
        "rr_intervals_ms": rr,
        "sensor_contact": bool(contact_detected) if contact_supported else None,
        "source": "garmin-ble",
    }


async def find_hr_device(name_filter, address, timeout=10):
    from bleak import BleakScanner

    if address:
        return address
    print(f"[ble] scanning {timeout}s for heart-rate sensors...")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    candidates = []
    for device, adv in found.values():
        name = device.name or adv.local_name or ""
        advertises_hr = HR_SERVICE_UUID in [u.lower() for u in adv.service_uuids]
        if advertises_hr or (name_filter and name_filter.lower() in name.lower()):
            candidates.append((device, name, adv.rssi, advertises_hr))
    return candidates


async def run_ble(publisher, args):
    from bleak import BleakClient

    result = await find_hr_device(args.name, args.address)
    if isinstance(result, list):
        for device, name, rssi, hr in result:
            print(f"  {device.address}  {name or '(sin nombre)':<25} rssi={rssi}  hr_service={hr}")
        if args.scan:
            return
        if not result:
            sys.exit("[ble] no heart-rate sensor found. Is 'Broadcast Heart Rate' enabled on the watch?")
        address = sorted(result, key=lambda c: (not c[3], -c[2]))[0][0].address
    else:
        address = result

    print(f"[ble] connecting to {address}...")
    async with BleakClient(address) as client:
        print("[ble] connected, subscribing to Heart Rate Measurement")
        await client.start_notify(HR_MEASUREMENT_UUID, lambda _, data: publisher.publish(parse_hr_measurement(data)))
        try:
            while client.is_connected:
                await asyncio.sleep(1)
        finally:
            if client.is_connected:
                await client.stop_notify(HR_MEASUREMENT_UUID)
    print("[ble] watch disconnected")


# ---------------------------------------------------------------------------
# Simulated workout
# ---------------------------------------------------------------------------
SESSION = [("rest", 30, 65), ("walk", 40, 105), ("run", 60, 160), ("sprint", 15, 192), ("cooldown", 40, 90)]


async def run_sim(publisher, args):
    bpm = 65.0
    t = 0
    while args.count == 0 or t < args.count:
        for phase, seconds, target in SESSION:
            for _ in range(seconds):
                # HR approaches the phase target like a first-order response + noise.
                bpm += (target - bpm) * 0.12 + random.gauss(0, 1.2) + 1.5 * math.sin(t / 7)
                beat_ms = 60000 / bpm
                publisher.publish({
                    "timestamp": now_iso(),
                    "heart_rate_bpm": int(round(bpm)),
                    "rr_intervals_ms": [round(random.gauss(beat_ms, beat_ms * 0.03)) for _ in range(2)],
                    "sensor_contact": True,
                    "activity": phase,
                    "source": "gateway-sim",
                })
                t += 1
                if args.count and t >= args.count:
                    return
                await asyncio.sleep(args.interval)


def main():
    p = argparse.ArgumentParser(description="Garmin heart rate -> MQTT -> AWS IoT Core")
    p.add_argument("--source", choices=["ble", "sim"], default="sim")
    p.add_argument("--config", default=str(HERE / "config.json"), help="written by terraform apply")
    p.add_argument("--name", default="", help="BLE: substring of the watch name (e.g. 'Garmin')")
    p.add_argument("--address", default="", help="BLE: connect directly to this MAC/UUID")
    p.add_argument("--scan", action="store_true", help="BLE: only list nearby heart-rate sensors")
    p.add_argument("--interval", type=float, default=1.0, help="sim: seconds between readings")
    p.add_argument("--count", type=int, default=0, help="sim: readings to send (0 = forever)")
    p.add_argument("--dry-run", action="store_true", help="print readings without connecting to AWS")
    args = p.parse_args()

    if args.dry_run:
        config = {"topic_prefix": "garmin", "client_id": "garmin-gateway"}
    else:
        try:
            config = json.loads(Path(args.config).read_text())
        except FileNotFoundError:
            sys.exit(f"{args.config} not found: run `terraform apply` first (it creates config.json and certs/)")

    publisher = None if args.scan else Publisher(config, args.dry_run)
    try:
        asyncio.run(run_ble(publisher, args) if args.source == "ble" else run_sim(publisher, args))
    except KeyboardInterrupt:
        pass
    finally:
        if publisher:
            publisher.close()


if __name__ == "__main__":
    main()
