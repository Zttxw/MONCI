"""Configuración centralizada del agente — leída de variables de entorno."""

import os

# --- Base de datos ---
DB_PATH = os.getenv("DB_PATH", "/app/data/monitor.db")

# --- Conectividad (ping) ---
PING_INTERVAL = int(os.getenv("PING_INTERVAL", "5"))
PING_TIMEOUT = int(os.getenv("PING_TIMEOUT", "2"))
PING_FAIL_THRESHOLD = int(os.getenv("PING_FAIL_THRESHOLD", "2"))
PING_TARGETS = ["8.8.8.8", "1.1.1.1"]  # Fijos; gateway e isp_hop se detectan automáticamente

# --- Velocidad (speedtest) ---
SPEED_INTERVAL_MINUTES = float(os.getenv("SPEED_INTERVAL_MINUTES", "15"))


# --- Ancho de banda (vnstat) ---
BANDWIDTH_INTERVAL_MINUTES = int(os.getenv("BANDWIDTH_INTERVAL_MINUTES", "5"))
# VNSTAT_INTERFACE se detecta automáticamente en utils.py

# --- DNS ---
DNS_CHECK_INTERVAL = int(os.getenv("DNS_CHECK_INTERVAL", "5"))
DNS_FAIL_THRESHOLD = int(os.getenv("DNS_FAIL_THRESHOLD", "2"))
DNS_TARGET_DOMAIN = os.getenv("DNS_TARGET_DOMAIN", "google.com")
DNS_SERVER = os.getenv("DNS_SERVER", "8.8.8.8")

# --- ISP hop (primer salto) ---
ISP_HOP_REFRESH_HOURS = float(os.getenv("ISP_HOP_REFRESH_HOURS", "6"))

# --- Probe Liviano de Velocidad ---
LIGHT_PROBE_INTERVAL_SECONDS = int(os.getenv("LIGHT_PROBE_INTERVAL_SECONDS", "60"))
LIGHT_PROBE_SIZE_MB = float(os.getenv("LIGHT_PROBE_SIZE_MB", "5.0"))
LIGHT_PROBE_STREAMS = int(os.getenv("LIGHT_PROBE_STREAMS", "3"))
LIGHT_PROBE_COOLDOWN_SECONDS = int(os.getenv("LIGHT_PROBE_COOLDOWN_SECONDS", "60"))
LIGHT_PROBE_URL = os.getenv(
    "LIGHT_PROBE_URL", "https://speed.cloudflare.com/__down?bytes=5242880"
)

# --- Umbrales de Degradación V1 (%) respecto al baseline oficial ---
DEGRADATION_THRESHOLD_PERCENT = float(os.getenv("DEGRADATION_THRESHOLD_PERCENT", "50.0"))
CRITICAL_THRESHOLD_PERCENT = float(os.getenv("CRITICAL_THRESHOLD_PERCENT", "25.0"))


# ============================================================================
# V2 — Motor de detección con FSM Mealy
# ============================================================================

# --- FSM ---
FSM_RECOVERY_K = int(os.getenv("FSM_RECOVERY_K", "3"))

# --- Frescura de sensores ---
# Máximo de segundos desde la última lectura para considerar un sensor "fresco".
# Si la lectura más reciente de L0 o L1 es más vieja que este valor, el
# discretizador no la combina (evita mezclar datos obsoletos).
SENSOR_FRESHNESS_SECONDS = int(os.getenv("SENSOR_FRESHNESS_SECONDS", "180"))

# --- L0 Sensor (conectividad / calidad) ---
L0_INTERVAL_SECONDS = int(os.getenv("L0_INTERVAL_SECONDS", "60"))
L0_HTTP_CHECK_URL = os.getenv(
    "L0_HTTP_CHECK_URL", "https://speed.cloudflare.com/__down?bytes=1024"
)
L0_PING_TARGET = os.getenv("L0_PING_TARGET", "8.8.8.8")
L0_DNS_TARGET = os.getenv("L0_DNS_TARGET", "google.com")
L0_DNS_SERVER = os.getenv("L0_DNS_SERVER", "8.8.8.8")

# --- L1 Sensor (micro-throughput — todos configurables para calibración) ---
L1_INTERVAL_SECONDS = int(os.getenv("L1_INTERVAL_SECONDS", "60"))
L1_PAYLOAD_SIZE_MB = float(os.getenv("L1_PAYLOAD_SIZE_MB", "5.0"))
L1_STREAMS = int(os.getenv("L1_STREAMS", "3"))
L1_SAMPLES_N = int(os.getenv("L1_SAMPLES_N", "1"))
L1_SAMPLE_INTERVAL_SECONDS = int(os.getenv("L1_SAMPLE_INTERVAL_SECONDS", "5"))
L1_AGGREGATION_METHOD = os.getenv("L1_AGGREGATION_METHOD", "median")  # "median" | "mean"
L1_DEGRADATION_THRESHOLD_PERCENT = float(os.getenv("L1_DEGRADATION_THRESHOLD_PERCENT", "50.0"))
L1_BASELINE_WINDOW = int(os.getenv("L1_BASELINE_WINDOW", "20"))
L1_BASELINE_MIN_COUNT = int(os.getenv("L1_BASELINE_MIN_COUNT", "20"))
_L1_DEFAULT_BYTES = int(L1_PAYLOAD_SIZE_MB * 1024 * 1024)
L1_URL = os.getenv("L1_URL", f"https://speed.cloudflare.com/__down?bytes={_L1_DEFAULT_BYTES}")

# --- L2 Sensor (confirmación pesada — Ookla bajo demanda) ---
# NOTA: Estos umbrales son PROVISIONALES, no valores definitivos.
# Se calibrarán/validarán con datos reales de V2 en producción.
L2_OUTAGE_THRESHOLD_PERCENT = float(os.getenv("L2_OUTAGE_THRESHOLD_PERCENT", "10.0"))
L2_DEGRADATION_THRESHOLD_PERCENT = float(os.getenv("L2_DEGRADATION_THRESHOLD_PERCENT", "50.0"))

# --- Fast.com Sensor & Evidence Engine (etapa intermedia de confirmación) ---
FAST_ENABLED = os.getenv("FAST_ENABLED", "True").lower() in ("true", "1", "yes")
FAST_TARGET_COUNT = int(os.getenv("FAST_TARGET_COUNT", "3"))
FAST_TIMEOUT_SECONDS = int(os.getenv("FAST_TIMEOUT_SECONDS", "10"))
FAST_DEFAULT_BASELINE_MBPS = float(os.getenv("FAST_DEFAULT_BASELINE_MBPS", "250.0"))
FAST_MIN_BASELINE_SAMPLES = int(os.getenv("FAST_MIN_BASELINE_SAMPLES", "5"))
FAST_DEGRADATION_THRESHOLD_PCT = float(os.getenv("FAST_DEGRADATION_THRESHOLD_PCT", "0.50"))


