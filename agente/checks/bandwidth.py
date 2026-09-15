"""Chequeo de ancho de banda — loop periódico consultando vnstat.

Reglas de diseño (ARCHITECTURE.md):
- Guarda DELTAS (consumo por ventana), no acumulados.
- Primer ciclo: no inserta, solo guarda lectura base.
- Si acumulado actual < anterior (reset de vnstat): delta = 0.
"""

import asyncio
import json
import logging
from typing import Optional

from config import BANDWIDTH_INTERVAL_MINUTES
from db import insert_uso_banda

logger = logging.getLogger(__name__)


async def _query_vnstat(interface: str) -> tuple[int, int]:
    """Consulta vnstat y retorna (bytes_rx, bytes_tx) acumulados totales."""
    proc = await asyncio.create_subprocess_exec(
        "vnstat", "--json", "-i", interface,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode().strip()
        raise RuntimeError(f"vnstat falló (rc={proc.returncode}): {err}")

    data = json.loads(stdout.decode())

    # vnstat JSON: interfaces[0].traffic.total.rx / .tx (en bytes)
    traffic = data["interfaces"][0]["traffic"]["total"]
    rx = traffic["rx"]
    tx = traffic["tx"]
    return rx, tx


async def _init_vnstat(interface: str) -> None:
    """Inicializa vnstat de forma idempotente (seguro en cada restart).

    Con restart: unless-stopped, esta función corre en cada arranque del
    container. Debe ser idempotente:
    1. No duplicar el proceso vnstatd si ya está corriendo
    2. No fallar si la interfaz ya fue añadida previamente
       (la DB de vnstat persiste en ./data/vnstat)
    """
    # 1. Verificar si vnstatd ya está corriendo (ej: restart del container
    #    donde el proceso padre murió pero /var/lib/vnstat persistió)
    check = await asyncio.create_subprocess_exec(
        "pidof", "vnstatd",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await check.wait()

    if check.returncode == 0:
        logger.info("vnstatd ya está corriendo, no se lanza otro")
    else:
        await asyncio.create_subprocess_exec(
            "vnstatd", "--daemon",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info("vnstatd daemon iniciado")
        # Esperar a que vnstatd cree la DB (solo necesario en primer arranque)
        await asyncio.sleep(3)

    # 2. Añadir interfaz — rc=1 con "already exists" es esperado en restarts
    proc = await asyncio.create_subprocess_exec(
        "vnstat", "--add", "-i", interface,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode().strip() or stderr.decode().strip()

    if proc.returncode == 0:
        logger.info("vnstat: interfaz %s añadida", interface)
    elif "already exists" in output.lower():
        logger.info("vnstat: interfaz %s ya registrada (esperado en restart)", interface)
    else:
        logger.warning("vnstat --add -i %s: rc=%d %s", interface, proc.returncode, output)

    # Esperar a que vnstatd recopile datos iniciales de la interfaz
    await asyncio.sleep(5)


async def bandwidth_loop(interface: str) -> None:
    """Loop principal de medición de ancho de banda. Corre indefinidamente."""
    interval_seconds = BANDWIDTH_INTERVAL_MINUTES * 60

    # Inicializar vnstat antes del primer ciclo
    await _init_vnstat(interface)

    logger.info(
        "Loop de ancho de banda iniciado — interfaz: %s — intervalo: %d min",
        interface, BANDWIDTH_INTERVAL_MINUTES,
    )

    # Estado previo para calcular deltas
    prev_bytes_in: Optional[int] = None
    prev_bytes_out: Optional[int] = None

    while True:
        try:
            curr_in, curr_out = await _query_vnstat(interface)

            if prev_bytes_in is not None and prev_bytes_out is not None:
                # Calcular deltas
                delta_in = curr_in - prev_bytes_in
                delta_out = curr_out - prev_bytes_out

                # Caso borde: reset de vnstat → delta negativo → tratar como 0
                if delta_in < 0:
                    logger.warning(
                        "vnstat reset detectado (bytes_in: %d → %d), delta = 0",
                        prev_bytes_in, curr_in,
                    )
                    delta_in = 0
                if delta_out < 0:
                    logger.warning(
                        "vnstat reset detectado (bytes_out: %d → %d), delta = 0",
                        prev_bytes_out, curr_out,
                    )
                    delta_out = 0

                insert_uso_banda(delta_in, delta_out)
            else:
                logger.info(
                    "Primer ciclo de bandwidth — lectura base: in=%d out=%d (no se inserta)",
                    curr_in, curr_out,
                )

            # Actualizar estado previo
            prev_bytes_in = curr_in
            prev_bytes_out = curr_out

        except Exception as e:
            logger.error("Error en medición de ancho de banda: %s", e)

        await asyncio.sleep(interval_seconds)
