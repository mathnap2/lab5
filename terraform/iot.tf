# ---------------------------------------------------------------------------
# AWS IoT Core: MQTT broker the gateway (PC + Garmin over BLE) publishes to.
# ---------------------------------------------------------------------------
data "aws_iot_endpoint" "ats" {
  endpoint_type = "iot:Data-ATS"
}

data "aws_caller_identity" "current" {}

locals {
  mqtt_topic_filter = "${var.mqtt_topic_prefix}/+/heartrate"
  iot_arn_prefix    = "arn:aws:iot:${var.aws_region}:${data.aws_caller_identity.current.account_id}"

  # Learner Lab cannot create IAM roles. The native IoT->Kinesis action needs a
  # role that iot.amazonaws.com can assume, so we only use it when LabRole
  # trusts IoT; otherwise IoT invokes a Lambda (resource policy, no role).
  lab_role_trusts_iot = strcontains(data.aws_iam_role.lab_role.assume_role_policy, "iot.amazonaws.com")
  iot_rule_direct     = var.iot_rule_mode == "direct" || (var.iot_rule_mode == "auto" && local.lab_role_trusts_iot)

  certs_dir = "${path.module}/../gateway/certs"
}

resource "aws_iot_thing" "gateway" {
  name = var.device_name
}

resource "aws_iot_certificate" "gateway" {
  active = true
}

resource "aws_iot_policy" "gateway" {
  name = "${var.project_name}-gateway-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "iot:Connect"
        Resource = "${local.iot_arn_prefix}:client/${var.device_name}"
      },
      {
        Effect   = "Allow"
        Action   = "iot:Publish"
        Resource = "${local.iot_arn_prefix}:topic/${var.mqtt_topic_prefix}/*"
      },
    ]
  })
}

resource "aws_iot_policy_attachment" "gateway" {
  policy = aws_iot_policy.gateway.name
  target = aws_iot_certificate.gateway.arn
}

resource "aws_iot_thing_principal_attachment" "gateway" {
  thing     = aws_iot_thing.gateway.name
  principal = aws_iot_certificate.gateway.arn
}

# --- Credentials + config written next to the gateway script ---------------

data "http" "amazon_root_ca" {
  url = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
}

resource "local_file" "root_ca" {
  content  = data.http.amazon_root_ca.response_body
  filename = "${local.certs_dir}/AmazonRootCA1.pem"
}

resource "local_file" "device_cert" {
  content  = aws_iot_certificate.gateway.certificate_pem
  filename = "${local.certs_dir}/device.pem.crt"
}

resource "local_sensitive_file" "device_key" {
  content  = aws_iot_certificate.gateway.private_key
  filename = "${local.certs_dir}/private.pem.key"
}

resource "local_file" "gateway_config" {
  filename = "${path.module}/../gateway/config.json"
  content = jsonencode({
    endpoint     = data.aws_iot_endpoint.ats.endpoint_address
    port         = 8883
    client_id    = var.device_name
    topic_prefix = var.mqtt_topic_prefix
    root_ca      = "certs/AmazonRootCA1.pem"
    certificate  = "certs/device.pem.crt"
    private_key  = "certs/private.pem.key"
  })
}

# --- Rule: MQTT topic -> Kinesis ------------------------------------------

resource "aws_iot_topic_rule" "hr_to_kinesis" {
  name        = replace("${var.project_name}_hr_to_kinesis", "-", "_")
  description = "Routes Garmin heart-rate MQTT messages into Kinesis"
  enabled     = true
  sql_version = "2016-03-23"

  # device_id comes from the topic, so the payload cannot spoof another device.
  sql = "SELECT *, topic(2) AS device_id, timestamp() AS iot_received_ms FROM '${local.mqtt_topic_filter}'"

  dynamic "kinesis" {
    for_each = local.iot_rule_direct ? [1] : []
    content {
      stream_name   = data.aws_kinesis_stream.hr_stream.name
      partition_key = "$${topic(2)}"
      role_arn      = data.aws_iam_role.lab_role.arn
    }
  }

  dynamic "lambda" {
    for_each = local.iot_rule_direct ? [] : [1]
    content {
      function_arn = aws_lambda_function.iot_to_kinesis[0].arn
    }
  }
}
