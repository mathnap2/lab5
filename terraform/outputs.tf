output "iot_endpoint" {
  description = "AWS IoT Core MQTT endpoint (TLS, port 8883)."
  value       = data.aws_iot_endpoint.ats.endpoint_address
}

output "mqtt_topic" {
  description = "Topic filter the IoT rule listens to."
  value       = local.mqtt_topic_filter
}

output "iot_rule_mode" {
  description = "direct = native IoT->Kinesis action, lambda = IoT->Lambda->Kinesis (Learner Lab fallback)."
  value       = local.iot_rule_direct ? "direct" : "lambda"
}

output "kinesis_stream_name" {
  value = data.aws_kinesis_stream.hr_stream.name
}

output "sqs_queue_url" {
  value = aws_sqs_queue.readings.url
}

output "sqs_dlq_url" {
  value = aws_sqs_queue.readings_dlq.url
}

output "dynamodb_table_name" {
  value = aws_dynamodb_table.readings.name
}

output "processor_lambda_name" {
  value = aws_lambda_function.processor.function_name
}

output "run_gateway_simulated" {
  description = "Publish simulated heart rate over MQTT from your PC."
  value       = "cd ../gateway && python gateway.py --source sim"
}

output "run_gateway_garmin" {
  description = "Publish the real heart rate broadcast by the Garmin watch over BLE."
  value       = "cd ../gateway && python gateway.py --source ble"
}

output "query_latest_readings" {
  value = "aws dynamodb query --table-name ${aws_dynamodb_table.readings.name} --key-condition-expression \"device_id = :d\" --expression-attribute-values '{\":d\":{\"S\":\"${var.device_name}\"}}' --no-scan-index-forward --max-items 5 --region ${var.aws_region} --profile ${var.aws_profile}"
}

output "analytics_backend" {
  value = var.analytics_backend
}

output "redshift_cluster_id" {
  value = local.use_redshift ? aws_redshift_cluster.analytics[0].cluster_identifier : null
}

output "redshift_query_example" {
  description = "Run a SQL query on Redshift through the Data API."
  value       = local.use_redshift ? "aws redshift-data execute-statement --cluster-identifier ${aws_redshift_cluster.analytics[0].cluster_identifier} --database ${var.redshift_database} --db-user ${var.redshift_master_username} --sql \"SELECT device_id, COUNT(*), AVG(heart_rate_bpm) FROM ${local.redshift_table} GROUP BY 1\" --region ${var.aws_region} --profile ${var.aws_profile}" : null
}

output "grafana_url" {
  description = "Grafana UI (takes ~3 min after apply to finish installing)."
  value       = "http://${aws_instance.grafana.public_ip}:3000"
}

output "grafana_admin_password" {
  description = "User: admin. Show it with: terraform output -raw grafana_admin_password"
  value       = random_password.grafana_admin.result
  sensitive   = true
}
