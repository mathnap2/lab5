"""IoT rule -> Kinesis.

Only deployed when the IoT rule cannot use its native Kinesis action (the
Learner Lab has no role that iot.amazonaws.com can assume). The rule passes
the SQL result (MQTT payload + device_id + iot_received_ms) as the event.
"""
import json
import os

import boto3

kinesis = boto3.client("kinesis")
STREAM_NAME = os.environ["STREAM_NAME"]


def handler(event, context):
    response = kinesis.put_record(
        StreamName=STREAM_NAME,
        Data=json.dumps(event).encode("utf-8"),
        PartitionKey=str(event.get("device_id", "unknown")),
    )
    return {"shard": response["ShardId"], "sequence": response["SequenceNumber"]}
