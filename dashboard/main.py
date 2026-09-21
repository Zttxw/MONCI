"""FastAPI dashboard — punto de entrada.

Sin CORS (no hay frontend en otro dominio).
Uvicorn en puerto 80, mapeado a 8085 por Docker.
"""

import logging
import sys

from fastapi import FastAPI

from db import wait_for_db
from routers import status, caidas, velocidad, reporte, dns, v2_api

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)

logger = logging.getLogger("dashboard")

# Esperar a que la DB esté disponible (defensa en profundidad contra riesgo #3)
logger.info("Esperando disponibilidad de la base de datos...")
wait_for_db()

app = FastAPI(
    title="Control Internet — Dashboard",
    description="Monitor de conectividad, velocidad y ancho de banda",
    version="2.0.0",
)

# Registrar routers
app.include_router(status.router)
app.include_router(caidas.router)
app.include_router(velocidad.router)
app.include_router(reporte.router)
app.include_router(dns.router)
app.include_router(v2_api.router)

logger.info("Dashboard V2 iniciado correctamente")

