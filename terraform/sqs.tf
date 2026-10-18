# Messages the processor fails to handle `sqs_max_receive_count` times end up
# here instead of being retried forever.
resource "aws_sqs_queue" "readings_dlq" {
  name                      = "${var.project_name}-readings-dlq"
  message_retention_seconds = 1209600 # 14 days
}

resource "aws_sqs_queue" "readings" {
  name                      = "${var.project_name}-readings"
  message_retention_seconds = 345600 # 4 days
  receive_wait_time_seconds = 20     # long polling
  # Must be >= the consumer Lambda timeout; AWS recommends 6x.
  visibility_timeout_seconds = var.lambda_timeout * 6

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.readings_dlq.arn
    maxReceiveCount     = var.sqs_max_receive_count
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "readings_dlq" {
  queue_url = aws_sqs_queue.readings_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.readings.arn]
  })
}
