"""Cloud-side simulated Garmin heart-rate sensors, invoked every minute.

Publishes through IoT Core (MQTT topic <prefix>/<device_id>/heartrate) so the
simulated data follows exactly the same path as the real watch: IoT rule ->
Kinesis -> SQS -> processor -> DynamoDB.
"""
import json
import os
import random
from datetime import datetime, timedelta, timezone

import boto3

iot = boto3.client("iot-data", endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}")
TOPIC_PREFIX = os.environ["TOPIC_PREFIX"]
DEVICE_IDS = [d.strip() for d in os.environ["DEVICE_IDS"].split(",") if d.strip()]
READINGS_PER_DEVICE = 12  # one every 5 s of the elapsed minute

# Rough "what is the wearer doing this minute" profiles: (mean bpm, stddev).
ACTIVITIES = {"rest": (68, 4), "walk": (102, 6), "run": (158, 8), "sprint": (192, 5)}
ACTIVITY_WEIGHTS = {"rest": 0.5, "walk": 0.3, "run": 0.17, "sprint": 0.03}


def rr_intervals(bpm, n=2):
    """Beat-to-beat intervals (ms) around 60000/bpm, with a bit of HRV."""
    base = 60000 / bpm
    return [round(random.gauss(base, base * 0.03)) for _ in range(n)]


def handler(event, context):
    now = datetime.now(timezone.utc)
    published = 0

    for device_id in DEVICE_IDS:
        activity = random.choices(list(ACTIVITY_WEIGHTS), weights=ACTIVITY_WEIGHTS.values())[0]
        mean, std = ACTIVITIES[activity]
        for i in range(READINGS_PER_DEVICE):
            bpm = max(35, min(220, round(random.gauss(mean, std))))
            ts = now - timedelta(seconds=5 * (READINGS_PER_DEVICE - 1 - i))
            payload = {
                "timestamp": ts.isoformat(timespec="milliseconds"),
                "heart_rate_bpm": bpm,
                "rr_intervals_ms": rr_intervals(bpm),
                "activity": activity,
                "sensor_contact": True,
                "source": "cloud-sim",
            }
            iot.publish(topic=f"{TOPIC_PREFIX}/{device_id}/heartrate", qos=1, payload=json.dumps(payload))
            published += 1

    print(json.dumps({"published": published}))
    return {"published": published}
