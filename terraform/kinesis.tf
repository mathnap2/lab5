# Receives every MQTT message routed by the IoT rule.
# Partition key = device_id, so readings of the same watch stay ordered
# inside a shard.
#
# Why not aws_kinesis_stream: on create the AWS provider always calls
# IncreaseStreamRetentionPeriod (even for the default 24 h), and the Learner
# Lab SCP explicitly denies that action. So the stream is created with the AWS
# CLI (CreateStream only) and read back with a data source.
locals {
  kinesis_stream_name = "${var.project_name}-stream"
  aws_cli_args        = "--region ${var.aws_region}${var.aws_profile != "" ? " --profile ${var.aws_profile}" : ""}"
}

resource "terraform_data" "hr_stream" {
  input = {
    name   = local.kinesis_stream_name
    shards = var.kinesis_shard_count
    args   = local.aws_cli_args
  }

  # Idempotent: reuses the stream if it already exists, then waits until ACTIVE.
  provisioner "local-exec" {
    command = "aws kinesis describe-stream-summary --stream-name ${self.input.name} ${self.input.args} --query StreamDescriptionSummary.StreamStatus --output text || aws kinesis create-stream --stream-name ${self.input.name} --shard-count ${self.input.shards} ${self.input.args} && aws kinesis wait stream-exists --stream-name ${self.input.name} ${self.input.args}"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "aws kinesis delete-stream --stream-name ${self.input.name} --enforce-consumer-deletion ${self.input.args}"
  }
}

data "aws_kinesis_stream" "hr_stream" {
  name       = local.kinesis_stream_name
  depends_on = [terraform_data.hr_stream]
}

# The previous apply left a half-created aws_kinesis_stream in the state.
# Forget it without deleting it: terraform_data above adopts that stream.
removed {
  from = aws_kinesis_stream.hr_stream

  lifecycle {
    destroy = false
  }
}
