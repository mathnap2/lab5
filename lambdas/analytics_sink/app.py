"""DynamoDB Streams -> Redshift / Timestream.

Every reading the processor stores in DynamoDB arrives here (NEW_IMAGE) and
is written to the analytics backend Grafana reads from:
  * redshift   -> one multi-row INSERT per batch through the Redshift Data API
  * timestream -> multi-measure records through WriteRecords (100 per call)

Only INSERT events are replicated: a MODIFY means the same reading was stored
again (SQS redelivery), and REMOVE is the DynamoDB TTL expiring old items.
"""
import logging
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeDeserializer

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BACKEND = os.environ["BACKEND"]
deserializer = TypeDeserializer()

COLUMNS = ("device_id", "ts", "heart_rate_bpm", "hr_zone", "hr_zone_name", "pct_max_hr", "rmssd_ms",
           "status", "alerts", "activity", "source", "end_to_end_latency_ms", "processed_at")


def to_reading(record):
    image = record["dynamodb"]["NewImage"]
    return {k: deserializer.deserialize(v) for k, v in image.items()}


def parse_ts(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Redshift
# ---------------------------------------------------------------------------
_SAFE_TEXT = re.compile(r"[^\w\-.:, ]")


def sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, datetime):
        return "'" + value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "'"
    # Values come from our own processor, but strip anything that could break
    # out of the quoted literal anyway.
    return "'" + _SAFE_TEXT.sub("", str(value))[:128] + "'"


def redshift_row(r):
    return (
        r["device_id"], parse_ts(r["timestamp"]), r["heart_rate_bpm"], r.get("hr_zone"), r.get("hr_zone_name"),
        r.get("pct_max_hr"), r.get("rmssd_ms"), r.get("status"), ",".join(r.get("alerts") or []),
        r.get("activity"), r.get("source"), r.get("end_to_end_latency_ms"),
        parse_ts(r["processed_at"]) if r.get("processed_at") else None,
    )


def write_redshift(readings):
    client = boto3.client("redshift-data")
    values = ",\n".join("(" + ", ".join(sql_literal(v) for v in redshift_row(r)) + ")" for r in readings)
    sql = f"INSERT INTO {os.environ['REDSHIFT_TABLE']} ({', '.join(COLUMNS)}) VALUES\n{values}"

    statement = client.execute_statement(
        ClusterIdentifier=os.environ["REDSHIFT_CLUSTER_ID"],
        Database=os.environ["REDSHIFT_DATABASE"],
        DbUser=os.environ["REDSHIFT_DB_USER"],
        Sql=sql,
    )
    # The Data API is asynchronous; wait so failures make Lambda retry the batch.
    while True:
        desc = client.describe_statement(Id=statement["Id"])
        if desc["Status"] == "FINISHED":
            return
        if desc["Status"] in ("FAILED", "ABORTED"):
            raise RuntimeError(f"Redshift INSERT {desc['Status']}: {desc.get('Error')}")
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Timestream
# ---------------------------------------------------------------------------
def timestream_record(r):
    measures = [
        ("heart_rate_bpm", r["heart_rate_bpm"], "BIGINT"),
        ("hr_zone", r.get("hr_zone"), "BIGINT"),
        ("pct_max_hr", r.get("pct_max_hr"), "DOUBLE"),
        ("rmssd_ms", r.get("rmssd_ms"), "DOUBLE"),
        ("end_to_end_latency_ms", r.get("end_to_end_latency_ms"), "BIGINT"),
        ("status", r.get("status"), "VARCHAR"),
        ("hr_zone_name", r.get("hr_zone_name"), "VARCHAR"),
        ("alerts", ",".join(r.get("alerts") or []) or "none", "VARCHAR"),
        ("activity", r.get("activity"), "VARCHAR"),
    ]
    return {
        "Dimensions": [
            {"Name": "device_id", "Value": str(r["device_id"])},
            {"Name": "source", "Value": str(r.get("source") or "unknown")},
        ],
        "MeasureName": "hr",
        "MeasureValueType": "MULTI",
        "MeasureValues": [
            {"Name": n, "Value": str(v), "Type": t} for n, v, t in measures if v is not None and v != ""
        ],
        "Time": str(int(parse_ts(r["timestamp"]).timestamp() * 1000)),
        "TimeUnit": "MILLISECONDS",
    }


def write_timestream(readings):
    client = boto3.client("timestream-write")
    records = [timestream_record(r) for r in readings]
    for start in range(0, len(records), 100):
        try:
            client.write_records(
                DatabaseName=os.environ["TIMESTREAM_DATABASE"],
                TableName=os.environ["TIMESTREAM_TABLE"],
                Records=records[start:start + 100],
            )
        except client.exceptions.RejectedRecordsException as exc:
            # e.g. timestamps older than the memory store retention: retrying
            # would never succeed, so log them and move on.
            logger.warning("Timestream rejected records: %s", exc.response.get("RejectedRecords"))


# ---------------------------------------------------------------------------
def handler(event, context):
    records = [r for r in event.get("Records", []) if r["eventName"] == "INSERT"]
    if not records:
        return {"batchItemFailures": []}

    readings = [to_reading(r) for r in records]
    try:
        (write_redshift if BACKEND == "redshift" else write_timestream)(readings)
    except Exception:
        logger.exception("Failed to write %d readings to %s", len(readings), BACKEND)
        # Retry from the first record of the batch (bisect narrows bad records down).
        return {"batchItemFailures": [{"itemIdentifier": records[0]["dynamodb"]["SequenceNumber"]}]}

    logger.info("Replicated %d readings to %s", len(readings), BACKEND)
    return {"batchItemFailures": []}
