"""Utilidades de red — detección automática de gateway, interfaz e ISP hop."""

import asyncio
import ipaddress
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config import ISP_HOP_REFRESH_HOURS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MutableValue — contenedor para valores que pueden cambiar entre ciclos
# ---------------------------------------------------------------------------

@dataclass
class MutableValue:
    """Contenedor para un valor que puede ser actualizado por otro loop asyncio.

    asyncio es single-threaded, así que no hay condición de carrera real,
    pero este patrón hace explícito que el valor puede cambiar entre ciclos.
    """

    _value: Optional[str] = None

    def get(self) -> Optional[str]:
        return self._value

    def set(self, v: Optional[str]) -> None:
        self._value = v


# ---------------------------------------------------------------------------
# Detección de gateway e interfaz
# ---------------------------------------------------------------------------

async def _run_ip_route() -> str:
    """Ejecuta `ip -4 route show default` y retorna el stdout."""
    proc = await asyncio.create_subprocess_exec(
        "ip", "-4", "route", "show", "default",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode().strip()
        raise RuntimeError(f"ip route falló (rc={proc.returncode}): {err}")

    output = stdout.decode().strip()
    if not output:
        raise RuntimeError("ip route no devolvió ruta por defecto")

    return output


async def get_default_gateway() -> str:
    """Detecta la IP del gateway por defecto parseando `ip route`.

    Ejemplo de output: 'default via 192.168.1.1 dev eth0 proto dhcp metric 100'
    """
    output = await _run_ip_route()
    parts = output.split()
    if "via" not in parts:
        raise RuntimeError(f"No se encontró 'via' en: {output}")

    gateway = parts[parts.index("via") + 1]
    logger.info("Gateway detectado: %s", gateway)
    return gateway


async def get_default_interface() -> str:
    """Detecta el nombre de la interfaz de red por defecto.

    Ejemplo de output: 'default via 192.168.1.1 dev eth0 proto dhcp metric 100'
    """
    output = await _run_ip_route()
    parts = output.split()
    if "dev" not in parts:
        raise RuntimeError(f"No se encontró 'dev' en: {output}")

    iface = parts[parts.index("dev") + 1]
    logger.info("Interfaz detectada: %s", iface)
    return iface


# ---------------------------------------------------------------------------
# Detección de ISP hop (primer salto fuera del gateway)
# ---------------------------------------------------------------------------

def _is_private_or_reserved(ip_str: str) -> bool:
    """Retorna True si la IP es privada, loopback, link-local o reservada.

    Nota: NO filtra CGNAT (100.64.0.0/10) ni IPs 10.x del ISP,
    porque en redes con CGNAT extenso son los únicos hops intermedios
    disponibles y siguen siendo útiles como tier intermedio.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
        return addr.is_private and not _is_cgnat(ip_str)
    except ValueError:
        return True  # si no es IP válida, descartarla


def _is_cgnat(ip_str: str) -> bool:
    """Retorna True si la IP está en el rango CGNAT (100.64.0.0/10)."""
    try:
        return ipaddress.ip_address(ip_str) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def _is_isp_internal(ip_str: str) -> bool:
    """Retorna True si la IP parece ser de la red interna del ISP (10.x CGNAT-like).

    En redes con CGNAT extenso, el ISP usa rangos 10.x.x.x para su red interna.
    Estos hops son válidos como tier intermedio.
    """
    try:
        return ipaddress.ip_address(ip_str) in ipaddress.ip_network("10.0.0.0/8")
    except ValueError:
        return False


# Regex para parsear IPs de la salida de traceroute
_TRACEROUTE_IP_RE = re.compile(r"(\d+\.\d+\.\d+\.\d+)")


async def _verify_ping(ip: str, timeout: int = 3) -> bool:
    """Verifica que una IP responda a ping directo (no solo a traceroute)."""
    proc = await asyncio.create_subprocess_exec(
        "ping", "-c", "2", "-W", str(timeout), ip,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait() == 0


async def get_isp_first_hop(gateway_ip: str) -> Optional[str]:
    """Detecta el primer salto fuera del gateway vía traceroute ICMP.

    Ejecuta: traceroute -I -n -m 15 -w 3 8.8.8.8
    Retorna la primera IP que no sea el gateway local Y que responda
    a ping directo, o None si no se encuentra un hop válido (con WARNING).

    Muchos routers CGNAT responden a ICMP Time Exceeded (traceroute) pero
    filtran ICMP Echo Request (ping). Por eso se verifica cada candidato
    con ping antes de darlo como válido — si no responde, se prueba el
    siguiente hop en la ruta.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "traceroute", "-I", "-n", "-m", "15", "-w", "3", "8.8.8.8",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode().strip()
            logger.warning("traceroute falló (rc=%d): %s", proc.returncode, err_msg)
            return None

        output = stdout.decode()
        logger.debug("traceroute output:\n%s", output)

        # Recoger todos los hops candidatos primero
        candidates = []
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("traceroute to"):
                continue
            if "*" in line and not _TRACEROUTE_IP_RE.search(line):
                continue

            match = _TRACEROUTE_IP_RE.search(line)
            if not match:
                continue

            hop_ip = match.group(1)

            if hop_ip == gateway_ip:
                continue

            try:
                addr = ipaddress.ip_address(hop_ip)
                if addr.is_loopback or addr.is_link_local:
                    continue
            except ValueError:
                continue

            if hop_ip == "8.8.8.8":
                continue

            if hop_ip not in candidates:
                candidates.append(hop_ip)

        # Probar cada candidato con ping hasta encontrar uno que responda
        for hop_ip in candidates:
            if await _verify_ping(hop_ip):
                logger.info("ISP first hop detectado y verificado con ping: %s", hop_ip)
                return hop_ip
            else:
                logger.debug(
                    "Hop candidato %s no responde a ping directo, probando siguiente...",
                    hop_ip,
                )

        logger.warning(
            "traceroute completado pero ningún hop fuera del gateway (%s) responde a ping. "
            "isp_hop no estará disponible.",
            gateway_ip,
        )
        return None

    except Exception as e:
        logger.warning("Error ejecutando traceroute para detectar isp_hop: %s", e)
        return None


# ---------------------------------------------------------------------------
# Re-detección periódica de ISP hop
# ---------------------------------------------------------------------------

async def isp_hop_refresh_loop(
    isp_hop_holder: MutableValue,
    gateway_ip: str,
    on_hop_changed=None,
) -> None:
    """Re-detecta isp_hop periódicamente para evitar obsolescencia.

    Si la IP del hop cambió, actualiza el holder y opcionalmente ejecuta
    un callback para que connectivity_loop maneje la transición.

    Args:
        isp_hop_holder: MutableValue con la IP actual del isp_hop.
        gateway_ip: IP del gateway local para excluirla del traceroute.
        on_hop_changed: callback async opcional(old_ip, new_ip) para
                        notificar a connectivity_loop del cambio.
    """
    interval = ISP_HOP_REFRESH_HOURS * 3600
    logger.info(
        "Loop de re-detección de ISP hop iniciado — intervalo: %.1f horas",
        ISP_HOP_REFRESH_HOURS,
    )

    while True:
        await asyncio.sleep(interval)
        try:
            new_hop = await get_isp_first_hop(gateway_ip)
            old_hop = isp_hop_holder.get()

            if new_hop != old_hop:
                logger.info("ISP hop cambió: %s → %s", old_hop, new_hop)
                isp_hop_holder.set(new_hop)

                if on_hop_changed is not None:
                    await on_hop_changed(old_hop, new_hop)
            else:
                logger.info("ISP hop sin cambios: %s", old_hop)

        except Exception as e:
            logger.warning("Error re-detectando ISP hop: %s", e)
