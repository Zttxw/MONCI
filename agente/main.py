"""Entry point del agente de monitoreo de internet.

Lanza 5 loops independientes en paralelo con asyncio.gather:
1. Conectividad (ping cada 5s) — máquina de estados con isp_hop dinámico
2. Velocidad (speedtest cada 30 min) — con medición de bufferbloat
3. Ancho de banda (vnstat cada 5 min)
4. Resolución DNS (cada 5s) — máquina de estados propia
5. Re-detección ISP hop (cada 6h) — actualiza destino dinámicamente

Ningún loop bloquea a los otros.
"""

import asyncio
import logging
import sys

from db import init_db, set_metadata
from utils import (
    get_default_gateway,
    get_default_interface,
    get_isp_first_hop,
    isp_hop_refresh_loop,
    MutableValue,
)
from checks.connectivity import connectivity_loop
from checks.speed import speed_loop
from checks.light_probe import light_probe_loop
from checks.bandwidth import bandwidth_loop
from checks.dns import dns_loop
from checks.shared import SpeedtestWindow

# Logging estructurado a stdout para `docker logs`
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)

logger = logging.getLogger("agente")


async def main() -> None:
    logger.info("=== Agente de monitoreo de internet iniciando ===")

    # 1. Inicializar base de datos
    init_db()

    # 2. Detectar gateway e interfaz de red
    try:
        gateway_ip = await get_default_gateway()
        interface = await get_default_interface()
    except RuntimeError as e:
        logger.critical("No se pudo detectar la red: %s", e)
        sys.exit(1)

    logger.info("Gateway: %s — Interfaz: %s", gateway_ip, interface)
    set_metadata("gateway_ip", gateway_ip)

    # 3. Detectar ISP first hop (puede ser None si no se encuentra)
    isp_hop_ip = await get_isp_first_hop(gateway_ip)
    isp_hop_holder = MutableValue()
    isp_hop_holder.set(isp_hop_ip)

    if isp_hop_ip:
        set_metadata("isp_hop", isp_hop_ip)
        logger.info("ISP first hop: %s", isp_hop_ip)
    else:
        logger.warning(
            "No se detectó ISP first hop — el monitoreo continuará sin ese tier"
        )

    # 4. Crear objeto compartido para bufferbloat
    window = SpeedtestWindow()

    # 5. Lanzar los 6 loops en paralelo (ninguno bloquea a los otros)
    logger.info("Lanzando loops de monitoreo...")
    await asyncio.gather(
        connectivity_loop(gateway_ip, isp_hop_holder, window),
        speed_loop(window),
        light_probe_loop(),
        bandwidth_loop(interface),
        dns_loop(),
        isp_hop_refresh_loop(isp_hop_holder, gateway_ip),
    )


if __name__ == "__main__":
    asyncio.run(main())
