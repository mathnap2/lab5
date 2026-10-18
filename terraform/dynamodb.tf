# NoSQL sink. One item per heart-rate reading:
#   PK device_id  (S)  -> all readings of a watch live in the same partition
#   SK timestamp  (S)  -> ISO-8601 UTC, so a range query returns them in order
resource "aws_dynamodb_table" "readings" {
  name         = "${var.project_name}-readings"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "device_id"
  range_key    = "timestamp"

  # Every new item is also pushed to the analytics sink (Redshift/Timestream).
  stream_enabled   = true
  stream_view_type = "NEW_IMAGE"

  attribute {
    name = "device_id"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  # Lets us query "all ALERT readings, newest first" without a scan.
  global_secondary_index {
    name            = "status-timestamp-index"
    hash_key        = "status"
    range_key       = "timestamp"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
