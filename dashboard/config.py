"""Configuración del dashboard."""

import os

DB_PATH = os.getenv("DB_PATH", "/app/data/monitor.db")
DB_URI = f"file:{DB_PATH}?mode=ro"

# Timezone para mostrar fechas en la UI (UTC en DB, local en display)
TZ = os.getenv("TZ", "America/Lima")
