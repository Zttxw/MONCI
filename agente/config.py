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
SPEED_INTERVAL_MINUTES = float(os.getenv("SPEED_INTERVAL_MINUTES", "30"))


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
LIGHT_PROBE_URL = os.getenv(
    "LIGHT_PROBE_URL", "https://speed.cloudflare.com/__down?bytes=5242880"
)

# --- Umbrales de Degradación (%) respecto al baseline oficial ---
DEGRADATION_THRESHOLD_PERCENT = float(os.getenv("DEGRADATION_THRESHOLD_PERCENT", "50.0"))
CRITICAL_THRESHOLD_PERCENT = float(os.getenv("CRITICAL_THRESHOLD_PERCENT", "25.0"))

