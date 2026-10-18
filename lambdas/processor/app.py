"""SQS -> DynamoDB processor.

Each SQS message is one heart-rate reading (JSON) that came from the watch
over MQTT. The function validates it, enriches it (HR zone, % of max HR,
RMSSD from RR intervals, status OK/ALERT, end-to-end latency) and writes it
to DynamoDB. Invalid or failing messages are reported individually so SQS
retries only those and, after maxReceiveCount, moves them to the DLQ.
"""
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
MAX_HR = 220 - int(os.environ.get("USER_AGE", "21"))
HR_HIGH = float(os.environ.get("HR_HIGH_ALERT", "185"))
HR_LOW = float(os.environ.get("HR_LOW_ALERT", "40"))
TTL_DAYS = int(os.environ.get("READINGS_TTL_DAYS", "7"))

REQUIRED_FIELDS = ("device_id", "timestamp", "heart_rate_bpm")

# Garmin-style 5 zones as % of max HR (lower bound of each zone).
ZONES = [(0.90, 5, "Maximum"), (0.80, 4, "Threshold"), (0.70, 3, "Aerobic"),
         (0.60, 2, "Easy"), (0.50, 1, "Warm up")]


class InvalidReading(ValueError):
    pass


def hr_zone(bpm):
    pct = bpm / MAX_HR
    for lower, number, name in ZONES:
        if pct >= lower:
            return number, name, pct
    return 0, "Rest", pct


def rmssd(rr):
    """Root mean square of successive RR differences: a standard HRV metric."""
    if len(rr) < 2:
        return None
    diffs = [(b - a) ** 2 for a, b in zip(rr, rr[1:])]
    return math.sqrt(sum(diffs) / len(diffs))


def parse_reading(body):
    try:
        reading = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidReading(f"body is not JSON: {exc}") from exc

    missing = [f for f in REQUIRED_FIELDS if f not in reading]
    if missing:
        raise InvalidReading(f"missing fields: {missing}")

    bpm = float(reading["heart_rate_bpm"])
    if not 25 <= bpm <= 240:
        raise InvalidReading(f"heart rate out of physiological range: {bpm}")

    rr = [float(x) for x in reading.get("rr_intervals_ms") or [] if 250 <= float(x) <= 2500]

    # Normalise the timestamp so it sorts correctly as the DynamoDB sort key.
    ts = datetime.fromisoformat(str(reading["timestamp"]).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    reading["timestamp"] = ts.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return reading, bpm, rr, ts


def build_item(message):
    reading, bpm, rr, ts = parse_reading(message["body"])
    zone, zone_name, pct = hr_zone(bpm)

    alerts = []
    if bpm > HR_HIGH:
        alerts.append("HIGH_HEART_RATE")
    if bpm < HR_LOW:
        alerts.append("LOW_HEART_RATE")
    if reading.get("sensor_contact") is False:
        alerts.append("NO_SKIN_CONTACT")

    now = time.time()
    hrv = rmssd(rr)
    item = {
        **reading,
        "rr_intervals_ms": rr,
        "hr_zone": zone,
        "hr_zone_name": zone_name,
        "pct_max_hr": round(pct * 100, 1),
        "status": "ALERT" if alerts else "OK",
        "alerts": alerts,
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "end_to_end_latency_ms": max(0, int((now - ts.timestamp()) * 1000)),
        "sqs_message_id": message["messageId"],
        "expires_at": int(now) + TTL_DAYS * 86400,
    }
    if hrv is not None:
        item["rmssd_ms"] = round(hrv, 1)
    # DynamoDB does not accept float; round-trip through JSON into Decimal.
    return json.loads(json.dumps(item), parse_float=Decimal)


def handler(event, context):
    failures = []
    stored = alerts = 0

    for message in event.get("Records", []):
        try:
            item = build_item(message)
            table.put_item(Item=item)
            stored += 1
            if item["status"] == "ALERT":
                alerts += 1
                logger.warning("ALERT %s %s %s bpm=%s", item["device_id"], item["timestamp"],
                               item["alerts"], item["heart_rate_bpm"])
        except InvalidReading as exc:
            logger.error("Invalid reading %s: %s", message["messageId"], exc)
            failures.append({"itemIdentifier": message["messageId"]})
        except Exception:
            logger.exception("Failed to store %s", message["messageId"])
            failures.append({"itemIdentifier": message["messageId"]})

    logger.info("Stored %d readings (%d alerts), %d failed", stored, alerts, len(failures))
    return {"batchItemFailures": failures}
