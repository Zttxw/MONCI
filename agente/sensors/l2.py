"""Sensor L2 — Ookla Speedtest de Alta Precisión (Bajo Demanda).

Ejecuta el binario oficial `speedtest --format=json` a demanda cuando la FSM entra en
estado CONFIRMANDO. Mide descarga, subida, ping inactivo y latencia bajo carga (bufferbloat).

Retorna una estructura `L2Reading` inmutable.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from checks.shared import SpeedtestWindow
from db import get_baseline_mbps_oficial
from engine.models import L2Reading
from sensors.base import BaseSensor


logger = logging.getLogger(__name__)


class L2Sensor(BaseSensor):
    """Sensor L2 bajo demanda para la confirmación formal de eventos de degradación."""

    def __init__(self, window: Optional[SpeedtestWindow] = None):
        super().__init__("L2_OoklaSpeedtest")
        self.window = window or SpeedtestWindow()

    async def measure(self) -> Optional[L2Reading]:
        """Ejecuta Ookla Speedtest CLI y captura métricas completas."""
        now = datetime.now(timezone.utc)
        logger.info("Sensor L2 disparado: Ejecutando Ookla Speedtest CLI...")

        try:
            # Notificar inicio de ventana de bufferbloat si window está disponible
            await self.window.start()

            proc = await asyncio.create_subprocess_exec(
                "speedtest",
                "--format=json",
                "--accept-license",
                "--accept-gdpr",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            # Notificar término de ventana de bufferbloat
            loaded_latency = await self.window.finish()

            if proc.returncode != 0:
                err_msg = stderr.decode().strip()
                logger.error("Ookla Speedtest CLI falló (código %d): %s", proc.returncode, err_msg)
                return None

            data = json.loads(stdout.decode())

            download_mbps = data["download"]["bandwidth"] * 8 / 1_000_000
            upload_mbps = data["upload"]["bandwidth"] * 8 / 1_000_000
            ping_ms = data["ping"]["latency"]

            server_info = data.get("server", {})
            server_id = server_info.get("id")
            server_name = server_info.get("name")

            baseline = get_baseline_mbps_oficial(limit=20, min_count=5)

            reading = L2Reading(
                timestamp=now,
                download_mbps=download_mbps,
                upload_mbps=upload_mbps,
                ping_ms=ping_ms,
                loaded_latency_ms=loaded_latency,
                baseline_mbps=baseline,
                server_id=server_id,
                server_name=server_name,
            )

            logger.info(
                "Sensor L2 OK: Download=%.2f Mbps, Upload=%.2f Mbps, Ping=%.1fms, Servidor=%s (id=%s)",
                download_mbps, upload_mbps, ping_ms, server_name, server_id
            )
            return reading

        except Exception as e:
            logger.error("Error inesperado en Sensor L2: %s", e)
            await self.window.finish()
            return None
        finally:
            # Iniciar cooldown pos-L2 para que L1 descarte muestras durante este periodo (e.g. 60s)
            from config import LIGHT_PROBE_COOLDOWN_SECONDS
            await self.window.start_cooldown(float(LIGHT_PROBE_COOLDOWN_SECONDS))
