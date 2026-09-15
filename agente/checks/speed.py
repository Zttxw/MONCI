"""Chequeo de velocidad — loop periódico con Ookla Speedtest CLI oficial.

Ejecuta el binario oficial `speedtest --format=json --accept-license --accept-gdpr`
de forma asíncrona mediante asyncio.create_subprocess_exec.

Integra medición de bufferbloat: marca ventana de speedtest en SpeedtestWindow
para que connectivity.py registre latencia de los pings durante el test.
"""

import asyncio
import json
import logging

from config import SPEED_INTERVAL_MINUTES
from db import insert_medicion_velocidad
from checks.shared import SpeedtestWindow
from checks.degradation import evaluate_degradation

logger = logging.getLogger(__name__)


async def _run_speedtest() -> tuple[float, float, float]:
    """Ejecuta el binario oficial de Ookla Speedtest CLI y retorna (down_mbps, up_mbps, ping_ms)."""
    proc = await asyncio.create_subprocess_exec(
        "speedtest",
        "--format=json",
        "--accept-license",
        "--accept-gdpr",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode().strip()
        raise RuntimeError(f"speedtest CLI falló con código {proc.returncode}: {err_msg}")

    data = json.loads(stdout.decode())

    # ookla CLI entrega bandwidth en bytes/s -> convertir a Mbps (bytes * 8 / 1_000_000)
    download_mbps = data["download"]["bandwidth"] * 8 / 1_000_000
    upload_mbps = data["upload"]["bandwidth"] * 8 / 1_000_000
    ping_ms = data["ping"]["latency"]

    return download_mbps, upload_mbps, ping_ms


async def speed_loop(window: SpeedtestWindow) -> None:
    """Loop principal de medición de velocidad. Corre indefinidamente.

    Coordina con connectivity_loop a través de SpeedtestWindow para
    capturar latencia bajo carga (bufferbloat).
    """
    interval_seconds = SPEED_INTERVAL_MINUTES * 60

    logger.info(
        "Loop de velocidad iniciado con Ookla Speedtest CLI — intervalo: %d minutos",
        SPEED_INTERVAL_MINUTES,
    )

    while True:
        try:
            logger.info("Iniciando medición de velocidad con Ookla Speedtest CLI...")

            # Abrir ventana de bufferbloat ANTES del speedtest
            await window.start()

            download, upload, ping_ms = await _run_speedtest()

            # Cerrar ventana y obtener promedio de latencia bajo carga
            latencia_carga = await window.finish()

            if latencia_carga is not None:
                logger.info(
                    "Bufferbloat: latencia bajo carga = %.1fms (promedio durante speedtest)",
                    latencia_carga,
                )

            insert_medicion_velocidad(download, upload, ping_ms, latencia_carga)

            # Evaluar degradación para el test oficial
            evaluate_degradation(download, fuente="oficial")

        except Exception as e:
            # Asegurar que la ventana se cierre si el speedtest falla
            await window.finish()
            logger.error("Error en medición de velocidad: %s", e)

        await asyncio.sleep(interval_seconds)
