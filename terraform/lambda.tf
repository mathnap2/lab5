# AWS Academy Learner Lab does not allow creating new IAM roles/policies, so
# every function reuses the pre-provisioned LabRole (it already has access to
# Kinesis, SQS, DynamoDB, IoT and CloudWatch Logs).
data "aws_iam_role" "lab_role" {
  name = "LabRole"
}

locals {
  lambdas_dir = "${path.module}/../lambdas"

  iot_ingest_name = "${var.project_name}-iot-to-kinesis"
  bridge_name     = "${var.project_name}-kinesis-to-sqs"
  processor_name  = "${var.project_name}-processor"
  simulator_name  = "${var.project_name}-hr-sim"
}

# ---------------------------------------------------------------------------
# Packages (boto3 ships with the Lambda runtime, so no pip install is needed)
# ---------------------------------------------------------------------------
data "archive_file" "iot_ingest_zip" {
  type        = "zip"
  source_dir  = "${local.lambdas_dir}/iot_to_kinesis"
  output_path = "${path.module}/build/iot_to_kinesis.zip"
}

data "archive_file" "bridge_zip" {
  type        = "zip"
  source_dir  = "${local.lambdas_dir}/kinesis_to_sqs"
  output_path = "${path.module}/build/kinesis_to_sqs.zip"
}

data "archive_file" "processor_zip" {
  type        = "zip"
  source_dir  = "${local.lambdas_dir}/processor"
  output_path = "${path.module}/build/processor.zip"
}

data "archive_file" "simulator_zip" {
  type        = "zip"
  source_dir  = "${local.lambdas_dir}/hr_sim"
  output_path = "${path.module}/build/hr_sim.zip"
}

# Log groups are created before the functions so Terraform owns them (and
# deletes them on destroy) instead of Lambda creating them implicitly.
resource "aws_cloudwatch_log_group" "iot_ingest" {
  count             = local.iot_rule_direct ? 0 : 1
  name              = "/aws/lambda/${local.iot_ingest_name}"
  retention_in_days = 3
}

resource "aws_cloudwatch_log_group" "bridge" {
  name              = "/aws/lambda/${local.bridge_name}"
  retention_in_days = 3
}

resource "aws_cloudwatch_log_group" "processor" {
  name              = "/aws/lambda/${local.processor_name}"
  retention_in_days = 3
}

resource "aws_cloudwatch_log_group" "simulator" {
  count             = var.enable_cloud_simulator ? 1 : 0
  name              = "/aws/lambda/${local.simulator_name}"
  retention_in_days = 3
}

# ---------------------------------------------------------------------------
# 0) IoT rule -> Kinesis (only when the rule cannot write to Kinesis directly)
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "iot_to_kinesis" {
  count = local.iot_rule_direct ? 0 : 1

  function_name = local.iot_ingest_name
  description   = "Invoked by the IoT rule; writes each MQTT message into Kinesis"

  filename         = data.archive_file.iot_ingest_zip.output_path
  source_code_hash = data.archive_file.iot_ingest_zip.output_base64sha256

  handler       = "app.handler"
  runtime       = "python3.12"
  architectures = ["x86_64"]
  role          = data.aws_iam_role.lab_role.arn
  memory_size   = 128
  timeout       = 10

  environment {
    variables = {
      STREAM_NAME = data.aws_kinesis_stream.hr_stream.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.iot_ingest]
}

resource "aws_lambda_permission" "allow_iot" {
  count         = local.iot_rule_direct ? 0 : 1
  statement_id  = "AllowExecutionFromIoTRule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.iot_to_kinesis[0].function_name
  principal     = "iot.amazonaws.com"
  source_arn    = aws_iot_topic_rule.hr_to_kinesis.arn
}

# ---------------------------------------------------------------------------
# 1) Kinesis -> SQS bridge
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "bridge" {
  function_name = local.bridge_name
  description   = "Reads heart-rate records from Kinesis and forwards them to SQS"

  filename         = data.archive_file.bridge_zip.output_path
  source_code_hash = data.archive_file.bridge_zip.output_base64sha256

  handler       = "app.handler"
  runtime       = "python3.12"
  architectures = ["x86_64"]
  role          = data.aws_iam_role.lab_role.arn
  memory_size   = var.lambda_memory_size
  timeout       = var.lambda_timeout

  environment {
    variables = {
      QUEUE_URL = aws_sqs_queue.readings.url
    }
  }

  depends_on = [aws_cloudwatch_log_group.bridge]
}

resource "aws_lambda_event_source_mapping" "kinesis_to_bridge" {
  event_source_arn  = data.aws_kinesis_stream.hr_stream.arn
  function_name     = aws_lambda_function.bridge.arn
  starting_position = "TRIM_HORIZON"

  batch_size                         = 100
  maximum_batching_window_in_seconds = 1
  maximum_retry_attempts             = 3
  bisect_batch_on_function_error     = true
  function_response_types            = ["ReportBatchItemFailures"]
}

# ---------------------------------------------------------------------------
# 2) SQS -> DynamoDB processor
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "processor" {
  function_name = local.processor_name
  description   = "Validates and enriches heart-rate readings from SQS and stores them in DynamoDB"

  filename         = data.archive_file.processor_zip.output_path
  source_code_hash = data.archive_file.processor_zip.output_base64sha256

  handler       = "app.handler"
  runtime       = "python3.12"
  architectures = ["x86_64"]
  role          = data.aws_iam_role.lab_role.arn
  memory_size   = var.lambda_memory_size
  timeout       = var.lambda_timeout

  environment {
    variables = {
      TABLE_NAME        = aws_dynamodb_table.readings.name
      USER_AGE          = tostring(var.user_age)
      HR_HIGH_ALERT     = tostring(var.hr_high_alert_bpm)
      HR_LOW_ALERT      = tostring(var.hr_low_alert_bpm)
      READINGS_TTL_DAYS = tostring(var.readings_ttl_days)
    }
  }

  depends_on = [aws_cloudwatch_log_group.processor]
}

resource "aws_lambda_event_source_mapping" "sqs_to_processor" {
  event_source_arn = aws_sqs_queue.readings.arn
  function_name    = aws_lambda_function.processor.arn

  batch_size                         = 10
  maximum_batching_window_in_seconds = 1
  function_response_types            = ["ReportBatchItemFailures"]
}

# ---------------------------------------------------------------------------
# 3) (Optional) simulated watches publishing over MQTT every minute
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "simulator" {
  count = var.enable_cloud_simulator ? 1 : 0

  function_name = local.simulator_name
  description   = "Simulated Garmin heart-rate sensors that publish to IoT Core (MQTT)"

  filename         = data.archive_file.simulator_zip.output_path
  source_code_hash = data.archive_file.simulator_zip.output_base64sha256

  handler       = "app.handler"
  runtime       = "python3.12"
  architectures = ["x86_64"]
  role          = data.aws_iam_role.lab_role.arn
  memory_size   = 128
  timeout       = 30

  environment {
    variables = {
      IOT_ENDPOINT = data.aws_iot_endpoint.ats.endpoint_address
      TOPIC_PREFIX = var.mqtt_topic_prefix
      DEVICE_IDS   = join(",", var.cloud_simulated_devices)
    }
  }

  depends_on = [aws_cloudwatch_log_group.simulator]
}

resource "aws_cloudwatch_event_rule" "simulator_schedule" {
  count               = var.enable_cloud_simulator ? 1 : 0
  name                = "${var.project_name}-sim-every-minute"
  description         = "Triggers the simulated heart-rate Lambda once per minute"
  schedule_expression = "rate(1 minute)"
}

resource "aws_cloudwatch_event_target" "simulator_schedule" {
  count = var.enable_cloud_simulator ? 1 : 0
  rule  = aws_cloudwatch_event_rule.simulator_schedule[0].name
  arn   = aws_lambda_function.simulator[0].arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  count         = var.enable_cloud_simulator ? 1 : 0
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.simulator[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.simulator_schedule[0].arn
}
