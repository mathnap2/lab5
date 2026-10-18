# ---------------------------------------------------------------------------
# Grafana OSS on EC2 (Amazon Managed Grafana needs IAM Identity Center, which
# the Learner Lab does not have). The instance uses LabInstanceProfile, so the
# Redshift / Timestream plugins authenticate with the instance role: no keys.
# Datasource and dashboard are provisioned at boot from user_data.
# ---------------------------------------------------------------------------
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

data "http" "my_ip" {
  url = "https://checkip.amazonaws.com"
}

resource "random_password" "grafana_admin" {
  length  = 16
  special = false
}

locals {
  grafana_cidr = var.grafana_allowed_cidr != "" ? var.grafana_allowed_cidr : "${trimspace(data.http.my_ip.response_body)}/32"

  grafana_plugin = local.use_redshift ? "grafana-redshift-datasource" : "grafana-timestream-datasource"
  datasource_ref = { type = local.grafana_plugin, uid = "hr-analytics" }

  datasource = {
    apiVersion = 1
    datasources = [merge(
      {
        name      = "HR analytics (${var.analytics_backend})"
        uid       = local.datasource_ref.uid
        type      = local.grafana_plugin
        isDefault = true
        editable  = true
      },
      # Objects with different shapes cannot share a conditional, so pick the
      # backend-specific block through its JSON form.
      jsondecode(local.use_redshift ? jsonencode({
        jsonData = {
          authType          = "default"
          defaultRegion     = var.aws_region
          clusterIdentifier = "${var.project_name}-redshift"
          database          = var.redshift_database
          dbUser            = var.redshift_master_username
        }
        }) : jsonencode({
        jsonData = {
          authType        = "default"
          defaultRegion   = var.aws_region
          defaultDatabase = local.timestream_db_name
          defaultTable    = local.timestream_tbl_name
          defaultMeasure  = "hr"
        }
      })),
    )]
  }

  # Same panels for both backends; only the SQL dialect changes.
  ts_from = "\"${local.timestream_db_name}\".\"${local.timestream_tbl_name}\""
  queries = local.use_redshift ? {
    heart_rate = "SELECT ts AS time, device_id, heart_rate_bpm FROM ${local.redshift_table} WHERE $__timeFilter(ts) ORDER BY 1"
    zones      = "SELECT hr_zone_name AS zona, COUNT(*) AS lecturas FROM ${local.redshift_table} WHERE $__timeFilter(ts) GROUP BY 1 ORDER BY 1"
    hrv        = "SELECT DATE_TRUNC('minute', ts) AS time, device_id, AVG(rmssd_ms) AS rmssd_ms FROM ${local.redshift_table} WHERE $__timeFilter(ts) AND rmssd_ms IS NOT NULL GROUP BY 1, 2 ORDER BY 1"
    latency    = "SELECT DATE_TRUNC('minute', ts) AS time, AVG(end_to_end_latency_ms) / 1000.0 AS latencia_s FROM ${local.redshift_table} WHERE $__timeFilter(ts) GROUP BY 1 ORDER BY 1"
    alerts     = "SELECT ts AS hora, device_id, heart_rate_bpm, alerts FROM ${local.redshift_table} WHERE $__timeFilter(ts) AND status = 'ALERT' ORDER BY ts DESC LIMIT 50"
    } : {
    heart_rate = "SELECT time, device_id, heart_rate_bpm FROM ${local.ts_from} WHERE $__timeFilter AND measure_name = 'hr' ORDER BY time"
    zones      = "SELECT hr_zone_name AS zona, COUNT(*) AS lecturas FROM ${local.ts_from} WHERE $__timeFilter AND measure_name = 'hr' GROUP BY hr_zone_name ORDER BY 1"
    hrv        = "SELECT bin(time, 1m) AS time, device_id, AVG(rmssd_ms) AS rmssd_ms FROM ${local.ts_from} WHERE $__timeFilter AND measure_name = 'hr' AND rmssd_ms IS NOT NULL GROUP BY bin(time, 1m), device_id ORDER BY 1"
    latency    = "SELECT bin(time, 1m) AS time, AVG(end_to_end_latency_ms) / 1000.0 AS latencia_s FROM ${local.ts_from} WHERE $__timeFilter AND measure_name = 'hr' GROUP BY bin(time, 1m) ORDER BY 1"
    alerts     = "SELECT time AS hora, device_id, heart_rate_bpm, alerts FROM ${local.ts_from} WHERE $__timeFilter AND measure_name = 'hr' AND status = 'ALERT' ORDER BY time DESC LIMIT 50"
  }

  # Redshift plugin: rawSQL + format (0 = time series, 1 = table).
  # Timestream plugin: rawQuery.
  target = {
    for k, q in local.queries : k => merge(
      { refId = "A", datasource = local.datasource_ref },
      jsondecode(local.use_redshift ? jsonencode({ rawSQL = q, format = contains(["zones", "alerts"], k) ? 1 : 0 }) : jsonencode({ rawQuery = q })),
    )
  }
  to_multi_series = [{ id = "prepareTimeSeries", options = { format = "multi" } }]

  dashboard = {
    uid           = "garmin-hr"
    title         = "Garmin – Frecuencia cardiaca en streaming"
    schemaVersion = 39
    refresh       = "10s"
    time          = { from = "now-30m", to = "now" }
    panels = [
      {
        id      = 1, type = "timeseries", title = "Frecuencia cardiaca (bpm)"
        gridPos = { x = 0, y = 0, w = 16, h = 9 }, datasource = local.datasource_ref
        targets = [local.target.heart_rate], transformations = local.to_multi_series
        fieldConfig = {
          defaults = {
            unit = "none", min = 40
            thresholds = { mode = "absolute", steps = [
              { color = "green", value = null },
              { color = "orange", value = 160 },
              { color = "red", value = var.hr_high_alert_bpm },
            ] }
            custom = { thresholdsStyle = { mode = "line+area" }, lineWidth = 2 }
          }
          overrides = []
        }
      },
      {
        id      = 2, type = "stat", title = "FC actual"
        gridPos = { x = 16, y = 0, w = 8, h = 9 }, datasource = local.datasource_ref
        targets = [local.target.heart_rate], transformations = local.to_multi_series
        options = { reduceOptions = { calcs = ["lastNotNull"], values = false }, colorMode = "background", graphMode = "area" }
        fieldConfig = {
          defaults = {
            unit = "none", decimals = 0
            thresholds = { mode = "absolute", steps = [
              { color = "blue", value = null },
              { color = "green", value = 60 },
              { color = "orange", value = 160 },
              { color = "red", value = var.hr_high_alert_bpm },
            ] }
          }
          overrides = []
        }
      },
      {
        id      = 3, type = "piechart", title = "Tiempo en cada zona de FC"
        gridPos = { x = 0, y = 9, w = 8, h = 8 }, datasource = local.datasource_ref
        targets = [local.target.zones]
        options = { reduceOptions = { calcs = ["lastNotNull"], values = true, fields = "/^lecturas$/" }, pieType = "donut", legend = { displayMode = "table", placement = "right", values = ["percent"] } }
      },
      {
        id          = 4, type = "timeseries", title = "HRV – RMSSD promedio por minuto (ms)"
        gridPos     = { x = 8, y = 9, w = 8, h = 8 }, datasource = local.datasource_ref
        targets     = [local.target.hrv], transformations = local.to_multi_series
        fieldConfig = { defaults = { unit = "ms" }, overrides = [] }
      },
      {
        id          = 5, type = "timeseries", title = "Latencia extremo a extremo (reloj → DynamoDB)"
        gridPos     = { x = 16, y = 9, w = 8, h = 8 }, datasource = local.datasource_ref
        targets     = [local.target.latency]
        fieldConfig = { defaults = { unit = "s" }, overrides = [] }
      },
      {
        id      = 6, type = "table", title = "Últimas alertas"
        gridPos = { x = 0, y = 17, w = 24, h = 8 }, datasource = local.datasource_ref
        targets = [local.target.alerts]
      },
    ]
  }
}

resource "aws_security_group" "grafana" {
  name        = "${var.project_name}-grafana"
  description = "Grafana web UI"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "Grafana UI"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = [local.grafana_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "grafana" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.grafana_instance_type
  subnet_id                   = data.aws_subnets.default.ids[0]
  vpc_security_group_ids      = [aws_security_group.grafana.id]
  associate_public_ip_address = true
  iam_instance_profile        = "LabInstanceProfile"

  user_data_replace_on_change = true
  user_data = templatefile("${path.module}/../grafana/user_data.sh.tftpl", {
    plugin          = local.grafana_plugin
    admin_password  = random_password.grafana_admin.result
    datasource_yaml = yamlencode(local.datasource)
    dashboard_json  = jsonencode(local.dashboard)
  })

  metadata_options {
    http_tokens = "required"
  }

  tags = {
    Name = "${var.project_name}-grafana"
  }
}
