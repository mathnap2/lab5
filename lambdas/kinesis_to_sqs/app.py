"""Kinesis -> SQS bridge.

Kinesis cannot push directly into SQS, so this function consumes the stream
and forwards every record as one SQS message. It reports partial batch
failures so only the records that failed (and the ones after them in the
shard) are retried by Lambda.
"""
import base64
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sqs = boto3.client("sqs")
QUEUE_URL = os.environ["QUEUE_URL"]
SQS_BATCH_LIMIT = 10


def _to_message(record):
    kinesis = record["kinesis"]
    body = base64.b64decode(kinesis["data"]).decode("utf-8")
    return {
        # SQS batch entry ids only allow [A-Za-z0-9_-] and max 80 chars.
        "Id": kinesis["sequenceNumber"][-80:],
        "MessageBody": body,
        "MessageAttributes": {
            "device_id": {"DataType": "String", "StringValue": kinesis["partitionKey"]},
            "kinesis_sequence": {"DataType": "String", "StringValue": kinesis["sequenceNumber"]},
            "kinesis_arrival": {
                "DataType": "Number",
                "StringValue": str(kinesis["approximateArrivalTimestamp"]),
            },
        },
    }


def handler(event, context):
    records = event.get("Records", [])
    failed_sequences = []

    for start in range(0, len(records), SQS_BATCH_LIMIT):
        chunk = records[start:start + SQS_BATCH_LIMIT]
        entries = [_to_message(r) for r in chunk]
        id_to_seq = {e["Id"]: r["kinesis"]["sequenceNumber"] for e, r in zip(entries, chunk)}

        try:
            response = sqs.send_message_batch(QueueUrl=QUEUE_URL, Entries=entries)
        except Exception:
            logger.exception("send_message_batch failed for %d records", len(entries))
            failed_sequences.extend(id_to_seq.values())
            continue

        for failure in response.get("Failed", []):
            logger.error("SQS rejected %s: %s", failure["Id"], failure.get("Message"))
            failed_sequences.append(id_to_seq[failure["Id"]])

    logger.info("Forwarded %d/%d records to SQS", len(records) - len(failed_sequences), len(records))

    # For Kinesis, Lambda retries from the lowest failed sequence number on.
    if failed_sequences:
        first_failed = min(failed_sequences, key=int)
        return {"batchItemFailures": [{"itemIdentifier": first_failed}]}
    return {"batchItemFailures": []}

