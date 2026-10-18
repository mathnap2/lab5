"""Builds Lab5_745671.pdf from the collected evidence (screenshots + query results)."""

import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "screenshots")
OUT = os.path.join(HERE, "..", "..", "Lab5_745671.pdf")

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="H1c", parent=styles["Heading1"], alignment=TA_CENTER))
styles.add(ParagraphStyle(name="Center", parent=styles["Normal"], alignment=TA_CENTER))
styles.add(ParagraphStyle(name="Caption", parent=styles["Normal"], fontSize=9, textColor=colors.grey, alignment=TA_CENTER, spaceAfter=14))
styles.add(ParagraphStyle(name="Mono", parent=styles["Normal"], fontName="Courier", fontSize=8, leading=10))
styles.add(ParagraphStyle(name="Body", parent=styles["Normal"], spaceAfter=6, leading=14))
styles.add(ParagraphStyle(name="Cell", parent=styles["Normal"], fontSize=8.5, leading=10.5))

story = []
PAGE_W = letter[0] - 1.4 * inch


def p(text, style="Body"):
    story.append(Paragraph(text, styles[style]))


def img(name, caption, max_h=4.3 * inch):
    from PIL import Image as PILImage

    path = os.path.join(SHOTS, name)
    w, h = PILImage.open(path).size
    ratio = h / w
    draw_w = PAGE_W
    draw_h = draw_w * ratio
    if draw_h > max_h:
        draw_h = max_h
        draw_w = draw_h / ratio
    story.append(Image(path, width=draw_w, height=draw_h))
    story.append(Paragraph(caption, styles["Caption"]))


def table(rows, col_widths, font_size=8.5):
    wrapped = [[Paragraph(str(c), styles["Cell"]) if i > 0 else c for c in row] for i, row in enumerate(rows)]
    t = Table(wrapped, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d3748")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f0")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t)


def mono(text):
    # Paragraph collapses runs of spaces, so keep indentation/alignment with &nbsp;.
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(" ", "&nbsp;")
    story.append(Paragraph(escaped.replace("\n", "<br/>"), styles["Mono"]))


# ---------------------------------------------------------------- Cover
story.append(Spacer(1, 0.6 * inch))
story.append(Paragraph("Lab 5 — Streaming de frecuencia cardiaca en AWS", styles["H1c"]))
story.append(Paragraph(
    "Garmin CIRQA &rarr; MQTT (IoT Core) &rarr; Kinesis &rarr; SQS &rarr; Lambda &rarr; DynamoDB "
    "&rarr; Redshift &rarr; Grafana, desplegado con Terraform", styles["Center"]))
story.append(Spacer(1, 0.4 * inch))

cover_rows = [
    ["Estudiante:", "Mathias Napoles"],
    ["ID de estudiante:", "745671"],
    ["Correo:", "mathias.napoles@iteso.mx"],
    ["Región AWS:", "us-east-1 (AWS Academy Learner Lab)"],
    ["Cuenta AWS:", "859410692445 (voclabs)"],
    ["Infraestructura:", "Terraform (carpeta lab5/terraform)"],
]
cover_table = Table(cover_rows, colWidths=[1.8 * inch, 4.2 * inch])
cover_table.setStyle(TableStyle([
    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 10),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ("TOPPADDING", (0, 0), (-1, -1), 6),
]))
story.append(cover_table)
story.append(Spacer(1, 0.4 * inch))

story.append(Paragraph("Objetivo", styles["Heading2"]))
p("Construir un servicio de <b>streaming</b> en AWS a partir de un sensor real: la "
  "<b>frecuencia cardiaca</b> que transmite una banda Garmin CIRQA. Las lecturas viajan por "
  "<b>MQTT</b> hasta AWS IoT Core, entran a un <b>Kinesis Data Stream</b>, pasan por una cola "
  "<b>SQS</b>, una función <b>Lambda</b> las valida y enriquece y las guarda en una base de datos "
  "<b>NoSQL (DynamoDB)</b>. Después se replican a <b>Amazon Redshift</b> para análisis y se "
  "visualizan en <b>Grafana</b>. Toda la infraestructura se despliega con un solo "
  "<font face='Courier'>terraform apply</font>.")

story.append(PageBreak())

# ---------------------------------------------------------------- Architecture
story.append(Paragraph("Arquitectura", styles["Heading1"]))
img("arquitectura.png", "Figura 1. Flujo completo de una lectura de frecuencia cardiaca, del reloj al dashboard.", max_h=5.6 * inch)

story.append(Paragraph("Componentes", styles["Heading2"]))
table([
    ["Servicio", "Recurso", "Función en el pipeline"],
    ["Garmin CIRQA + gateway", "gateway/gateway.py", "Lee la FC por Bluetooth LE (Heart Rate Service 0x180D) o la simula, y la publica por MQTT/TLS (puerto 8883) con un certificado X.509."],
    ["AWS IoT Core", "Thing, certificado, policy, topic rule", "Broker MQTT. La regla <font face='Courier'>SELECT *, topic(2) AS device_id ... FROM 'garmin/+/heartrate'</font> manda cada mensaje a Kinesis."],
    ["Kinesis Data Stream", "lab5-hr-stream-stream", "Stream ordenado por <font face='Courier'>device_id</font> (clave de partición), 1 shard, 24 h de retención."],
    ["Lambda puente", "lab5-hr-stream-kinesis-to-sqs", "Kinesis no escribe directo a SQS: esta Lambda reenvía los registros en lotes de 10."],
    ["SQS + DLQ", "lab5-hr-stream-readings (-dlq)", "Desacopla ingesta y procesamiento. Tras 3 fallos el mensaje pasa a la cola de errores."],
    ["Lambda procesadora", "lab5-hr-stream-processor", "Valida (25–240 bpm), calcula zona de FC, % de FC máx., HRV (RMSSD), alertas y latencia; escribe en DynamoDB. Reporta fallos parciales del lote."],
    ["DynamoDB (NoSQL)", "lab5-hr-stream-readings", "PK <font face='Courier'>device_id</font>, SK <font face='Courier'>timestamp</font>, GSI por <font face='Courier'>status</font>, TTL de 7 días, Streams activados."],
    ["Lambda analytics-sink", "lab5-hr-stream-analytics-sink", "Lee DynamoDB Streams (solo INSERT) y hace un INSERT por lote en Redshift con la Data API."],
    ["Amazon Redshift", "lab5-hr-stream-redshift (ra3.large)", "Almacén analítico. Tabla <font face='Courier'>public.hr_readings</font> con DISTKEY device_id y SORTKEY ts."],
    ["Grafana OSS", "EC2 t3.small + LabInstanceProfile", "Dashboard provisionado automáticamente con el plugin de Redshift (sin llaves de acceso)."],
    ["Simulador (opcional)", "Lambda hr-sim + EventBridge", "Cada minuto publica lecturas de dos dispositivos simulados por el mismo camino MQTT."],
], [1.35 * inch, 1.75 * inch, 3.9 * inch])

story.append(PageBreak())

# ---------------------------------------------------------------- Flow
story.append(Paragraph("Flujo de una lectura", styles["Heading1"]))
steps = [
    ("Captura", "La banda Garmin CIRQA, con <i>Transmitir frecuencia cardiaca</i> activado, envía la FC por "
     "Bluetooth LE. El gateway en la PC recibe cada notificación, decodifica los bpm y los intervalos RR "
     "(tiempo entre latidos) y arma un JSON."),
    ("MQTT", "El gateway publica el JSON en <font face='Courier'>garmin/garmin-gateway/heartrate</font> con "
     "QoS 1 sobre TLS mutuo: la PC valida a AWS con Amazon Root CA y AWS valida a la PC con su certificado. "
     "La policy solo permite conectarse como <font face='Courier'>garmin-gateway</font> y publicar en "
     "<font face='Courier'>garmin/*</font>."),
    ("Ingesta", "La regla de IoT agrega <font face='Courier'>device_id</font> (tomado del topic, así el payload no "
     "puede suplantar otro dispositivo) y la hora de llegada, y escribe el registro en Kinesis."),
    ("Desacople", "La Lambda puente consume lotes de hasta 100 registros de Kinesis y los envía a SQS en lotes "
     "de 10. Si SQS rechaza algo, reporta el primer número de secuencia fallido para que Lambda reintente "
     "desde ahí."),
    ("Procesamiento", "La Lambda procesadora toma mensajes de SQS de 10 en 10, valida y enriquece cada lectura "
     "y la guarda en DynamoDB. Un mensaje inválido se reporta individualmente; el resto del lote sigue."),
    ("Analítica", "DynamoDB Streams entrega cada item nuevo a la Lambda analytics-sink, que junta hasta 200 "
     "lecturas (o 10 s) y hace un solo INSERT de varias filas en Redshift. Ignora MODIFY (reentregas) y "
     "REMOVE (TTL), así no se duplican datos."),
    ("Visualización", "Grafana consulta Redshift con el rol de la instancia EC2 y refresca el dashboard cada "
     "10 s: FC en vivo, FC actual, tiempo por zona, HRV, latencia y alertas."),
]
for i, (title, text) in enumerate(steps, 1):
    p(f"<b>{i}. {title}.</b> {text}")

story.append(Spacer(1, 0.1 * inch))
story.append(Paragraph("Ejemplo de mensaje MQTT", styles["Heading2"]))
mono('{"timestamp": "2026-10-17T17:34:49.357+00:00", "heart_rate_bpm": 107,\n'
     ' "rr_intervals_ms": [546, 552], "sensor_contact": true,\n'
     ' "activity": "walk", "source": "gateway-sim"}')

story.append(Spacer(1, 0.1 * inch))
story.append(Paragraph("Ejemplo de item en DynamoDB (enriquecido por la procesadora)", styles["Heading2"]))
mono('{"device_id": "garmin-sim-01", "timestamp": "2026-10-17T17:25:20.587+00:00",\n'
     ' "heart_rate_bpm": 163, "hr_zone": 4, "hr_zone_name": "Threshold", "pct_max_hr": 81.9,\n'
     ' "rmssd_ms": 7, "rr_intervals_ms": [366, 359], "status": "OK", "alerts": [],\n'
     ' "iot_received_ms": 1792257975597, "processed_at": "2026-10-17T17:26:38.585+00:00",\n'
     ' "sqs_message_id": "a1166da6-...", "expires_at": 1792862798, "source": "cloud-sim"}')

story.append(PageBreak())

# ---------------------------------------------------------------- Evidence
story.append(Paragraph("Evidencia del funcionamiento", styles["Heading1"]))
p("Capturas de la consola de AWS Academy Learner Lab y de Grafana tomadas con el pipeline "
  "recibiendo datos del gateway (<font face='Courier'>garmin-gateway</font>) y del simulador en la "
  "nube (<font face='Courier'>garmin-sim-01</font>, <font face='Courier'>garmin-sim-02</font>).")

story.append(Paragraph("1. Sensor y MQTT", styles["Heading2"]))
img("00_gateway_mqtt_publish.png", "Gateway conectado al endpoint ATS de IoT Core publicando una lectura por segundo con sus intervalos RR.", max_h=3.0 * inch)
img("01_iot_mqtt_test_client.jpg", "Cliente de prueba MQTT de IoT Core suscrito a garmin/#: llegan los payloads del gateway.")
story.append(PageBreak())

story.append(Paragraph("2. Regla de IoT Core", styles["Heading2"]))
img("02_iot_rule_sql.jpg", "Regla lab5_hr_stream_hr_to_kinesis activa, con la instrucción SQL sobre garmin/+/heartrate.")
img("03_iot_rule_action_kinesis.jpg", "La acción de la regla es Kinesis Data Stream (acción nativa: LabRole confía en iot.amazonaws.com).")
story.append(PageBreak())

story.append(Paragraph("3. Kinesis Data Stream", styles["Heading2"]))
img("04_kinesis_data_viewer.jpg", "Visor de datos del stream: registros con clave de partición = device_id y el JSON de cada lectura.")

story.append(Paragraph("4. SQS", styles["Heading2"]))
img("05_sqs_queues.jpg", "Cola principal con mensajes en tránsito (siendo procesados) y DLQ vacía: ningún mensaje falló.", max_h=3.2 * inch)
story.append(PageBreak())

story.append(Paragraph("5. Funciones Lambda", styles["Heading2"]))
img("06_lambda_functions.jpg", "Las cuatro funciones del lab (Python 3.12), todas usando LabRole.")
img("07_lambda_processor_trigger_sqs.jpg", "Lambda procesadora con SQS como desencadenador.")
story.append(PageBreak())
img("08_cloudwatch_processor_logs.jpg", "Logs de la procesadora en CloudWatch: \"Stored 7 readings (0 alerts), 0 failed\".")

story.append(Paragraph("6. DynamoDB (base NoSQL)", styles["Heading2"]))
img("09_dynamodb_items.jpg", "Items en la tabla lab5-hr-stream-readings (PK device_id, SK timestamp).", max_h=3.6 * inch)
story.append(PageBreak())
img("10_dynamodb_item_enriched.jpg", "Un item completo: zona de FC, % de FC máx., RMSSD, latencia, ID del mensaje SQS y TTL.")

story.append(Paragraph("7. Amazon Redshift", styles["Heading2"]))
img("11_redshift_cluster.jpg", "Cluster lab5-hr-stream-redshift: ra3.large, 1 nodo, estado Available.", max_h=3.6 * inch)
story.append(PageBreak())
img("11b_redshift_query_editor.jpg", "Query Editor v2 sobre public.hr_readings: lecturas, FC promedio, FC máxima y alertas por dispositivo.")

story.append(Paragraph("8. Grafana en EC2", styles["Heading2"]))
img("12_ec2_grafana_instance.jpg", "Instancia EC2 lab5-hr-stream-grafana (t3.small) en ejecución, 3/3 comprobaciones de estado.", max_h=3.4 * inch)
story.append(PageBreak())
img("13_grafana_dashboard.jpg", "Dashboard: FC en vivo con umbrales, FC actual por dispositivo, tiempo por zona, HRV (RMSSD) y latencia de extremo a extremo.", max_h=4.6 * inch)
img("14_grafana_alerts.jpg", "Tabla de alertas: lecturas del gateway por encima de 185 bpm marcadas como HIGH_HEART_RATE.", max_h=4.0 * inch)

story.append(PageBreak())

# ---------------------------------------------------------------- Results
story.append(Paragraph("Resultados", styles["Heading1"]))
p("Consulta de agregación ejecutada en Redshift al final de la prueba (captura 11b):")
table([
    ["device_id", "Lecturas", "FC promedio", "FC máxima", "Alertas"],
    ["garmin-gateway", "1509", "118", "197", "31"],
    ["garmin-sim-01", "497", "104", "178", "0"],
    ["garmin-sim-02", "492", "100", "198", "10"],
], [2.0 * inch, 1.1 * inch, 1.2 * inch, 1.2 * inch, 1.0 * inch], font_size=9)
story.append(Spacer(1, 0.15 * inch))
p("Las casi 2,500 lecturas recorrieron todo el pipeline sin pérdidas: la DLQ terminó con 0 mensajes "
  "y los logs de la procesadora reportan <font face='Courier'>0 failed</font>. Las 41 alertas "
  "corresponden a los tramos de sprint de la sesión simulada (FC &gt; 185 bpm). Del reloj a DynamoDB "
  "una lectura tarda alrededor de 1–3 s; para aparecer en Grafana se suman unos 10–20 s por la ventana "
  "de lotes de DynamoDB Streams. Los dispositivos simulados en la nube muestran latencias mayores "
  "porque el simulador fecha cada lectura hacia atrás, repartida en el minuto anterior.")

story.append(Paragraph("¿Por qué DynamoDB y Redshift juntos?", styles["Heading2"]))
p("DynamoDB es la base <b>operativa</b> (OLTP): recibe cada lectura al instante con escrituras de "
  "milisegundos y consulta el historial de un dispositivo por su clave; cumple el requisito NoSQL. "
  "Redshift es la base <b>analítica</b> (OLAP): es columnar, recibe lotes y resuelve agregaciones "
  "como promedios o tiempo por zona, que es lo que necesita Grafana. Es el patrón típico en AWS: "
  "base operativa &rarr; stream &rarr; data warehouse.")

story.append(PageBreak())

# ---------------------------------------------------------------- Terraform
story.append(Paragraph("Despliegue con Terraform", styles["Heading1"]))
p("Toda la infraestructura (IoT Core, Kinesis, SQS, DynamoDB, 5 Lambdas, Redshift, EC2 de Grafana, "
  "EventBridge) se crea con <font face='Courier'>terraform apply</font> desde "
  "<font face='Courier'>lab5/terraform</font>, usando el perfil de AWS CLI "
  "<font face='Courier'>academy</font>. Terraform también genera el certificado del dispositivo y "
  "escribe <font face='Courier'>gateway/config.json</font> y <font face='Courier'>gateway/certs/</font>, "
  "por lo que el gateway funciona sin pasos manuales.")
mono("""lab5/
  terraform/   providers, variables, iot, kinesis, sqs, dynamodb, lambda,
               analytics, grafana, outputs
  lambdas/     kinesis_to_sqs, processor, analytics_sink, hr_sim, iot_to_kinesis
  gateway/     gateway.py (BLE real o simulación), requirements.txt
  grafana/     user_data.sh.tftpl (instala y provisiona Grafana)""")
story.append(Spacer(1, 0.1 * inch))
p("Salida de <font face='Courier'>terraform apply</font> (outputs):")
mono("""analytics_backend     = "redshift"
dynamodb_table_name   = "lab5-hr-stream-readings"
grafana_url           = "http://3.239.95.5:3000"
iot_endpoint          = "a16eqxbgoiv5ce-ats.iot.us-east-1.amazonaws.com"
iot_rule_mode         = "direct"
kinesis_stream_name   = "lab5-hr-stream-stream"
mqtt_topic            = "garmin/+/heartrate"
processor_lambda_name = "lab5-hr-stream-processor"
redshift_cluster_id   = "lab5-hr-stream-redshift"
sqs_dlq_url           = "https://sqs.us-east-1.amazonaws.com/859410692445/lab5-hr-stream-readings-dlq"
sqs_queue_url         = "https://sqs.us-east-1.amazonaws.com/859410692445/lab5-hr-stream-readings\"""")

story.append(Paragraph("Restricciones del Learner Lab y cómo se resolvieron", styles["Heading2"]))
table([
    ["Problema", "Solución"],
    ["No se pueden crear roles IAM.", "Todas las Lambdas usan <b>LabRole</b>; la EC2 de Grafana usa <b>LabInstanceProfile</b>. Terraform detecta si LabRole confía en iot.amazonaws.com para usar la acción nativa IoT &rarr; Kinesis (si no, usa una Lambda intermedia)."],
    ["El SCP del lab niega <font face='Courier'>kinesis:IncreaseStreamRetentionPeriod</font>, que el provider de AWS llama siempre al crear un stream.", "El stream se crea con la AWS CLI desde Terraform (<font face='Courier'>terraform_data</font> + <font face='Courier'>local-exec</font>, solo CreateStream) y se lee con <font face='Courier'>data \"aws_kinesis_stream\"</font>."],
    ["<font face='Courier'>Invalid node type: dc2.large</font> en Redshift.", "AWS ya no ofrece nodos dc2; se usa <b>ra3.large</b>."],
    ["Redshift: <font face='Courier'>ModifyCluster AutoFailover</font> rechazado tras crear el cluster.", "<font face='Courier'>multi_az = false</font> y <font face='Courier'>lifecycle { ignore_changes = all }</font>: el cluster no se modifica en el lab."],
    ["Amazon Managed Grafana requiere IAM Identity Center.", "Grafana OSS en EC2, instalada y provisionada por <font face='Courier'>user_data</font>."],
    ["Kinesis no puede escribir directo en SQS.", "Lambda puente con reporte de fallos parciales por número de secuencia."],
    ["Dos gateways con el mismo client id se desconectaban mutuamente.", "AWS IoT solo permite una conexión por client id; se ejecuta un solo gateway."],
], [2.6 * inch, 4.4 * inch])

story.append(Spacer(1, 0.2 * inch))
p("Al terminar, la infraestructura se elimina con <font face='Courier'>terraform destroy</font> para no "
  "consumir el presupuesto del Learner Lab (Redshift, EC2 y Kinesis cobran por hora).")

doc = SimpleDocTemplate(
    OUT,
    pagesize=letter,
    topMargin=0.7 * inch,
    bottomMargin=0.7 * inch,
    leftMargin=0.7 * inch,
    rightMargin=0.7 * inch,
    title="Lab 5 - Streaming de frecuencia cardiaca en AWS",
    author="Mathias Napoles",
)
doc.build(story)
print("PDF built at:", os.path.abspath(OUT))
