variable "aws_profile" {
  description = "AWS CLI profile to use (AWS Academy Learner Lab exports credentials under this profile)."
  type        = string
  default     = "academy"
}

variable "aws_region" {
  description = "AWS region to deploy into. AWS Academy Learner Lab is normally locked to us-east-1."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefix used to name every resource created by this lab."
  type        = string
  default     = "lab5-hr-stream"
}

# --- IoT Core / MQTT --------------------------------------------------------

variable "device_name" {
  description = "IoT Thing name (and MQTT client id) of the gateway that relays the Garmin heart-rate sensor."
  type        = string
  default     = "garmin-gateway"
}

variable "mqtt_topic_prefix" {
  description = "Devices publish to <prefix>/<device_id>/heartrate."
  type        = string
  default     = "garmin"
}

variable "iot_rule_mode" {
  description = <<-EOT
    How the IoT rule delivers MQTT messages to Kinesis:
      "auto"   -> "direct" if LabRole trusts iot.amazonaws.com, otherwise "lambda"
      "direct" -> native IoT rule Kinesis action (needs a role IoT can assume)
      "lambda" -> IoT rule invokes a small Lambda that calls PutRecord (no IoT role needed)
  EOT
  type        = string
  default     = "auto"

  validation {
    condition     = contains(["auto", "direct", "lambda"], var.iot_rule_mode)
    error_message = "iot_rule_mode must be auto, direct or lambda."
  }
}

# --- Kinesis / SQS ----------------------------------------------------------

variable "kinesis_shard_count" {
  description = "Number of shards for the Kinesis Data Stream (1 shard = 1 MB/s or 1000 records/s of ingest)."
  type        = number
  default     = 1
}

variable "sqs_max_receive_count" {
  description = "Times a message can be received from the main queue before being moved to the DLQ."
  type        = number
  default     = 3
}

# --- Lambda / processing ----------------------------------------------------

variable "lambda_timeout" {
  description = "Timeout (seconds) for the pipeline Lambda functions."
  type        = number
  default     = 30
}

variable "lambda_memory_size" {
  description = "Memory (MB) allocated to each pipeline Lambda function."
  type        = number
  default     = 256
}

variable "user_age" {
  description = "Age of the person wearing the watch; used to estimate max HR (220 - age) and the HR zones."
  type        = number
  default     = 21
}

variable "hr_high_alert_bpm" {
  description = "Heart rate (bpm) above which a reading is flagged as an alert."
  type        = number
  default     = 185
}

variable "hr_low_alert_bpm" {
  description = "Heart rate (bpm) below which a reading is flagged as an alert."
  type        = number
  default     = 40
}

variable "readings_ttl_days" {
  description = "Days a reading is kept in DynamoDB before TTL deletes it automatically."
  type        = number
  default     = 7
}

# --- Cloud simulator --------------------------------------------------------

variable "enable_cloud_simulator" {
  description = "If true, an EventBridge rule invokes a Lambda every minute that publishes simulated heart-rate readings over MQTT (IoT Core), so the pipeline has data without a watch connected."
  type        = bool
  default     = true
}

variable "cloud_simulated_devices" {
  description = "Device IDs simulated by the cloud simulator Lambda."
  type        = list(string)
  default     = ["garmin-sim-01", "garmin-sim-02"]
}

# --- Analytics (Redshift / Timestream) + Grafana ----------------------------

variable "analytics_backend" {
  description = "Where DynamoDB readings are replicated for Grafana: \"redshift\" or \"timestream\" (Timestream LiveAnalytics may be closed to new accounts)."
  type        = string
  default     = "redshift"

  validation {
    condition     = contains(["redshift", "timestream"], var.analytics_backend)
    error_message = "analytics_backend must be redshift or timestream."
  }
}

variable "redshift_node_type" {
  description = "Redshift node type. dc2 nodes are no longer offered; ra3.large is the smallest RA3 node."
  type        = string
  default     = "ra3.large"
}

variable "redshift_database" {
  description = "Database created inside the Redshift cluster."
  type        = string
  default     = "hranalytics"
}

variable "redshift_master_username" {
  description = "Master user of the Redshift cluster (also used by Lambda and Grafana through temporary credentials)."
  type        = string
  default     = "awsuser"
}

variable "grafana_instance_type" {
  description = "EC2 instance type for the Grafana server."
  type        = string
  default     = "t3.small"
}

variable "grafana_allowed_cidr" {
  description = "CIDR allowed to open Grafana on port 3000. Empty = only your current public IP."
  type        = string
  default     = ""
}
