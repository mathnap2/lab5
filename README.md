# Lab 5 – Streaming de frecuencia cardiaca (Garmin → MQTT → Kinesis → SQS → Lambda → DynamoDB → Redshift/Timestream → Grafana)

## Arquitectura

```
 Garmin (BLE Heart Rate 0x180D)
        │  Bluetooth LE
        ▼
 PC gateway (gateway/gateway.py)  ──MQTT/TLS 8883──►  AWS IoT Core
                                                        │ IoT Rule: SELECT *, topic(2) AS device_id
                                                        ▼        FROM 'garmin/+/heartrate'
                                              Kinesis Data Stream
                                                        │ event source mapping
                                                        ▼
                                          Lambda kinesis-to-sqs (puente)
                                                        ▼
                                              SQS readings ──(3 fallos)──► SQS DLQ
                                                        │ event source mapping
                                                        ▼
                                          Lambda processor (valida + enriquece)
                                                        ▼
                                               DynamoDB readings
                                                        │ DynamoDB Streams (NEW_IMAGE)
                                                        ▼
                                          Lambda analytics-sink
                                                        ▼
                                   Redshift (default)  ó  Timestream
                                                        │ Data API / Query API (rol LabInstanceProfile)
                                                        ▼
                                          Grafana OSS en EC2 :3000 (dashboard ya listo)
```

* **Sensor:** frecuencia cardiaca del reloj Garmin. Puede ser **real**: el reloj transmite por BLE con el perfil estándar *Heart Rate Service* y el gateway lo lee con `bleak`. También puede ser **simulado**: una sesión de reposo → caminar → correr → sprint → enfriamiento.
* **MQTT:** el gateway se autentica en AWS IoT Core con un certificado X.509. Terraform genera ese certificado y lo guarda en `gateway/certs/`.
* **IoT Rule → Kinesis:** el Learner Lab no deja crear roles IAM. Si `LabRole` confía en `iot.amazonaws.com`, se usa la acción nativa de Kinesis. Si no, se usa una Lambda intermedia (`iot-to-kinesis`). Terraform lo decide solo (`iot_rule_mode = "auto"`).
* **Kinesis → SQS:** Kinesis no puede escribir directo a SQS, así que una Lambda puente reenvía los registros en lotes de 10.
* **Processor:** calcula la zona de FC (estilo Garmin, con FC máx = 220 − edad), el % de FC máx, el HRV (RMSSD a partir de los intervalos RR), las alertas (FC alta, FC baja, sin contacto con la piel) y la latencia de extremo a extremo. Después guarda la lectura en DynamoDB. Los mensajes inválidos se reintentan y terminan en la DLQ.
* **DynamoDB:** PK `device_id`, SK `timestamp`. Tiene un GSI `status-timestamp-index` para consultar las alertas, y TTL de 7 días.
* **DynamoDB → analítica:** DynamoDB Streams manda cada lectura nueva a la Lambda `analytics-sink`. Esa Lambda hace un `INSERT` de varias filas por lote en **Redshift** usando la Data API, o un `WriteRecords` en **Timestream**. Ignora los eventos MODIFY (reentregas de SQS) y REMOVE (borrados por TTL), así que no se duplican datos.
* **Redshift:** cluster de un solo nodo, privado. La tabla `public.hr_readings` (`DISTKEY device_id`, `SORTKEY ts`) la crea Terraform con `aws_redshiftdata_statement`.
* **Timestream (opción):** base `lab5_hr_stream_db`, tabla `hr_readings`, registros multi-measure `hr` con las dimensiones `device_id` y `source`. ⚠️ AWS cerró Timestream for LiveAnalytics a clientes nuevos, así que es probable que la cuenta del lab no lo pueda crear. Por eso el default es Redshift.
* **Grafana:** Amazon Managed Grafana requiere IAM Identity Center, que el Learner Lab no tiene, así que Terraform levanta Grafana OSS en una EC2 con `LabInstanceProfile`. El plugin se autentica con el rol de la instancia, sin llaves. En el arranque se instalan el plugin, la fuente de datos y el dashboard *"Garmin – Frecuencia cardiaca en streaming"*: FC en vivo, FC actual, tiempo por zona, HRV (RMSSD), latencia de extremo a extremo y una tabla de alertas. El puerto 3000 solo se abre para tu IP pública actual.
* **Simulador en la nube (opcional):** EventBridge invoca cada minuto una Lambda que publica por MQTT (`iot-data:Publish`) con dos relojes simulados. Así el pipeline tiene datos aunque tu PC esté apagada.

## Estructura

```
lab5/
├── terraform/          providers, variables, iot, kinesis, sqs, dynamodb, lambda, outputs
├── lambdas/
│   ├── iot_to_kinesis/ (solo si la regla IoT no puede escribir directo a Kinesis)
│   ├── kinesis_to_sqs/
│   ├── processor/
│   ├── analytics_sink/ DynamoDB Streams -> Redshift / Timestream
│   └── hr_sim/         simulador en la nube
├── grafana/            user_data.sh.tftpl (instala y provisiona Grafana)
└── gateway/            gateway.py (BLE real o simulación) + requirements.txt
                        config.json y certs/ los genera terraform apply
```

## Despliegue

1. En el Learner Lab: **Start Lab** → **AWS Details** → copia las credenciales a `~/.aws/credentials` bajo el perfil `[academy]` (igual que en el lab 4).
2. Despliega:
   ```bash
   cd lab5/terraform
   terraform init
   terraform apply
   ```
3. Instala las dependencias del gateway:
   ```bash
   cd ../gateway
   pip install -r requirements.txt
   ```

> `terraform apply` tarda unos 10 min, sobre todo por el cluster de Redshift. Después, Grafana necesita unos 3 min más para terminar de instalarse en la EC2.

## Abrir Grafana

```bash
terraform output grafana_url
terraform output -raw grafana_admin_password
```
Usuario `admin`. El dashboard está en la carpeta **Lab 5** y también es la página de inicio.

## Enviar datos

### Simulado (no necesita reloj)
```bash
python gateway.py --source sim
```

### Real con el Garmin
1. En el reloj activa **Transmitir frecuencia cardiaca / Broadcast Heart Rate**. Según el modelo está en el menú de controles o en *Sensores y accesorios → FC de muñeca*. Si tu modelo no tiene esa opción, no expone la FC por BLE estándar: usa el modo `sim`.
2. Con el Bluetooth de la PC encendido:
   ```bash
   python gateway.py --source ble --scan          # lista los sensores de FC cercanos
   python gateway.py --source ble --name Garmin   # se conecta y publica cada latido por MQTT
   ```

Prueba sin AWS: `python gateway.py --source sim --dry-run`

Una vez que haya tráfico (el simulador en la nube o el gateway), las lecturas tardan unos 10–20 s en aparecer en Grafana. Ese retraso viene de la ventana de lotes del stream de DynamoDB.

## Verificar

```bash
# Últimas lecturas del gateway
terraform output -raw query_latest_readings

# Alertas
aws dynamodb query --table-name lab5-hr-stream-readings --index-name status-timestamp-index --key-condition-expression "#s = :a" --expression-attribute-names '{"#s":"status"}' --expression-attribute-values '{":a":{"S":"ALERT"}}' --profile academy

# Redshift: lecturas por dispositivo (luego: aws redshift-data get-statement-result --id <Id>)
terraform output -raw redshift_query_example

# Logs del procesador
aws logs tail /aws/lambda/lab5-hr-stream-processor --follow --profile academy

# Mensajes en la DLQ
aws sqs get-queue-attributes --queue-url $(terraform output -raw sqs_dlq_url) --attribute-names ApproximateNumberOfMessages --profile academy
```

También puedes ver el tráfico MQTT en vivo en la consola: **IoT Core → MQTT test client → Subscribe** a `garmin/#`.

## Variables útiles

| Variable | Default | Uso |
|---|---|---|
| `user_age` | 21 | FC máx = 220 − edad (zonas) |
| `hr_high_alert_bpm` / `hr_low_alert_bpm` | 185 / 40 | Umbrales de alerta |
| `enable_cloud_simulator` | true | Lambda simuladora cada minuto |
| `iot_rule_mode` | auto | `direct` / `lambda` para forzar el modo |
| `analytics_backend` | redshift | `redshift` o `timestream` |
| `redshift_node_type` | ra3.large | Nodo más pequeño disponible (dc2 ya no existe) |
| `grafana_instance_type` | t3.small | Tamaño de la EC2 de Grafana |
| `grafana_allowed_cidr` | (tu IP) | Quién puede abrir Grafana |

Ejemplo: `terraform apply -var="user_age=22" -var="analytics_backend=timestream"`

## Destruir

```bash
terraform destroy
```
Hazlo al terminar para no gastar crédito del lab (el shard de Kinesis, el cluster de Redshift y la EC2 de Grafana cobran por hora).
