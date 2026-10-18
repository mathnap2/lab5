# ---------------------------------------------------------------------------
# DynamoDB Streams -> analytics sink Lambda -> Redshift or Timestream
# Grafana reads from whichever backend is selected (see grafana.tf).
# ---------------------------------------------------------------------------
locals {
  use_redshift   = var.analytics_backend == "redshift"
  use_timestream = var.analytics_backend == "timestream"

  analytics_sink_name = "${var.project_name}-analytics-sink"
  redshift_table      = "public.hr_readings"
  timestream_db_name  = replace("${var.project_name}-db", "-", "_")
  timestream_tbl_name = "hr_readings"
}

# --- Redshift (single-node provisioned cluster) ----------------------------

resource "random_password" "redshift_master" {
  count            = local.use_redshift ? 1 : 0
  length           = 24
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
  special          = true
  override_special = "!#$%^&*()-_=+"
}

resource "aws_redshift_cluster" "analytics" {
  count = local.use_redshift ? 1 : 0

  cluster_identifier = "${var.project_name}-redshift"
  database_name      = var.redshift_database
  master_username    = var.redshift_master_username
  master_password    = random_password.redshift_master[0].result
  node_type          = var.redshift_node_type
  cluster_type       = "single-node"

  # Lambda and Grafana talk to Redshift through the Data API (HTTPS + IAM
  # temporary credentials), so the cluster does not need to be public.
  publicly_accessible                 = false
  skip_final_snapshot                 = true
  automated_snapshot_retention_period = 1
  multi_az                            = false

  # After creation AWS fills in computed settings (Multi-AZ/AutoFailover on
  # RA3, etc.) and the provider then sends a ModifyCluster that mixes
  # AutoFailover with other changes, which AWS rejects. The cluster never
  # needs to change during the lab, so never modify it in place.
  lifecycle {
    ignore_changes = all
  }
}

resource "aws_redshiftdata_statement" "create_table" {
  count = local.use_redshift ? 1 : 0

  cluster_identifier = aws_redshift_cluster.analytics[0].cluster_identifier
  database           = var.redshift_database
  db_user            = var.redshift_master_username
  sql                = <<-SQL
    CREATE TABLE IF NOT EXISTS ${local.redshift_table} (
      device_id             VARCHAR(64)  NOT NULL,
      ts                    TIMESTAMP    NOT NULL,
      heart_rate_bpm        SMALLINT     NOT NULL,
      hr_zone               SMALLINT,
      hr_zone_name          VARCHAR(16),
      pct_max_hr            DECIMAL(5,1),
      rmssd_ms              DECIMAL(7,1),
      status                VARCHAR(8),
      alerts                VARCHAR(128),
      activity              VARCHAR(16),
      source                VARCHAR(32),
      end_to_end_latency_ms INTEGER,
      processed_at          TIMESTAMP
    )
    DISTKEY (device_id)
    SORTKEY (ts);
  SQL
}

# --- Timestream for LiveAnalytics ------------------------------------------

resource "aws_timestreamwrite_database" "analytics" {
  count         = local.use_timestream ? 1 : 0
  database_name = local.timestream_db_name
}

resource "aws_timestreamwrite_table" "readings" {
  count         = local.use_timestream ? 1 : 0
  database_name = aws_timestreamwrite_database.analytics[0].database_name
  table_name    = local.timestream_tbl_name

  retention_properties {
    memory_store_retention_period_in_hours  = 24
    magnetic_store_retention_period_in_days = 7
  }
}

# --- Sink Lambda fed by the DynamoDB stream --------------------------------

data "archive_file" "analytics_sink_zip" {
  type        = "zip"
  source_dir  = "${local.lambdas_dir}/analytics_sink"
  output_path = "${path.module}/build/analytics_sink.zip"
}

resource "aws_cloudwatch_log_group" "analytics_sink" {
  name              = "/aws/lambda/${local.analytics_sink_name}"
  retention_in_days = 3
}

resource "aws_lambda_function" "analytics_sink" {
  function_name = local.analytics_sink_name
  description   = "Replicates new DynamoDB readings into ${var.analytics_backend} for Grafana"

  filename         = data.archive_file.analytics_sink_zip.output_path
  source_code_hash = data.archive_file.analytics_sink_zip.output_base64sha256

  handler       = "app.handler"
  runtime       = "python3.12"
  architectures = ["x86_64"]
  role          = data.aws_iam_role.lab_role.arn
  memory_size   = var.lambda_memory_size
  timeout       = 60

  environment {
    variables = {
      BACKEND             = var.analytics_backend
      REDSHIFT_CLUSTER_ID = local.use_redshift ? aws_redshift_cluster.analytics[0].cluster_identifier : ""
      REDSHIFT_DATABASE   = var.redshift_database
      REDSHIFT_DB_USER    = var.redshift_master_username
      REDSHIFT_TABLE      = local.redshift_table
      TIMESTREAM_DATABASE = local.timestream_db_name
      TIMESTREAM_TABLE    = local.timestream_tbl_name
    }
  }

  depends_on = [aws_cloudwatch_log_group.analytics_sink]
}

resource "aws_lambda_event_source_mapping" "dynamodb_to_analytics" {
  event_source_arn  = aws_dynamodb_table.readings.stream_arn
  function_name     = aws_lambda_function.analytics_sink.arn
  starting_position = "TRIM_HORIZON"

  # Bigger, less frequent batches = fewer Redshift INSERTs.
  batch_size                         = 200
  maximum_batching_window_in_seconds = 10
  maximum_retry_attempts             = 5
  bisect_batch_on_function_error     = true
  function_response_types            = ["ReportBatchItemFailures"]

  # Do not start replicating before the target table exists.
  depends_on = [
    aws_redshiftdata_statement.create_table,
    aws_timestreamwrite_table.readings,
  ]
}
