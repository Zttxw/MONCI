"""Chequeo de conectividad — máquina de estados UP/DOWN por destino.

Reglas de diseño (ARCHITECTURE.md):
- Ping 4 destinos en paralelo cada 5s: 8.8.8.8, 1.1.1.1, gateway, isp_hop
- Solo escribe en DB cuando un destino CAMBIA de estado
- 2 fallos consecutivos → DOWN (evita falsos positivos)
- origen con 3 niveles: red_local, isp_primer_salto, isp_general
- Para gateway, origen siempre es "red_local" por definición
- Estado inicial: todos UP, consecutive_failures = 0
- Latencia bajo carga: reutiliza latencia del ping a 8.8.8.8 existente
  (cero tráfico adicional) cuando SpeedtestWindow está activa
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config import PING_INTERVAL, PING_TIMEOUT, PING_FAIL_THRESHOLD, PING_TARGETS
from db import (
    close_evento_caida,
    get_all_open_eventos_caida,
    get_open_evento_caida,
    insert_evento_caida,
)
from checks.shared import SpeedtestWindow
from utils import MutableValue

logger = logging.getLogger(__name__)

# Regex para parsear latencia de la salida de ping
_LATENCY_RE = re.compile(r"time[=<](\d+(?:\.\d+)?)\s*ms")

# Destino usado para capturar latencia de bufferbloat
_BUFFERBLOAT_TARGET = "8.8.8.8"


@dataclass
class DestinationState:
    """Estado de un destino de ping.

    Invariante de inicialización: todos los destinos arrancan en estado UP
    con consecutive_failures = 0. No se abre evento de caída hasta que
    el umbral de fallos consecutivos se cumpla normalmente, a menos que
    se recupere un evento abierto previo desde SQLite.
    """

    host: str
    is_gateway: bool = False
    is_isp_hop: bool = False
    status: str = "UP"                          # estado inicial: UP
    consecutive_failures: int = 0               # sin fallos al inicio
    current_event_id: Optional[int] = None      # sin evento abierto


async def _ping_with_latency(
    host: str,
    timeout: int = PING_TIMEOUT,
) -> tuple[bool, Optional[float]]:
    """Ejecuta un ping y retorna (success, latency_ms).

    Parsea time=X.XX ms del stdout para obtener la latencia sin ejecutar
    un ping adicional. Si el ping falla o no se puede parsear la latencia,
    retorna (False, None) o (True, None) respectivamente.
    """
    proc = await asyncio.create_subprocess_exec(
        "ping", "-c", "1", "-W", str(timeout), host,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout_bytes, _ = await proc.communicate()
    success = proc.returncode == 0

    latency_ms = None
    if success and stdout_bytes:
        match = _LATENCY_RE.search(stdout_bytes.decode(errors="replace"))
        if match:
            latency_ms = float(match.group(1))

    return success, latency_ms


def _determine_origen(
    dest: DestinationState,
    gateway_state: DestinationState,
    isp_hop_state: Optional[DestinationState],
) -> str:
    """Determina el origen de una caída con 3 niveles.

    - Gateway: siempre "red_local" por definición.
    - ISP hop: "red_local" si gateway DOWN, "isp_primer_salto" si gateway UP.
    - Externos: depende del estado de gateway e isp_hop:
        - gateway DOWN → "red_local"
        - gateway UP, isp_hop DOWN → "isp_primer_salto"
        - gateway UP, isp_hop UP (o no detectado) → "isp_general"
    """
    if dest.is_gateway:
        return "red_local"

    if dest.is_isp_hop:
        if gateway_state.status == "UP":
            return "isp_primer_salto"
        else:
            return "red_local"

    # Destinos externos (8.8.8.8, 1.1.1.1)
    if gateway_state.status == "DOWN":
        return "red_local"

    if isp_hop_state is not None and isp_hop_state.status == "DOWN":
        return "isp_primer_salto"

    return "isp_general"


async def _process_ping_result(
    dest: DestinationState,
    success: bool,
    gateway_state: DestinationState,
    isp_hop_state: Optional[DestinationState],
) -> None:
    """Procesa el resultado de un ping y ejecuta transiciones de estado."""
    now = datetime.now(timezone.utc)

    if success:
        if dest.status == "DOWN":
            # Transición DOWN → UP: cerrar evento de caída
            if dest.current_event_id is not None:
                close_evento_caida(
                    dest.current_event_id, now, cierre_tipo="recuperacion",
                )
                logger.info(
                    "▲ %s recuperado (UP) — evento %d cerrado (recuperacion)",
                    dest.host, dest.current_event_id,
                )
            dest.status = "UP"
            dest.current_event_id = None

        # UP y éxito: reset contador
        dest.consecutive_failures = 0

    else:
        if dest.status == "UP":
            dest.consecutive_failures += 1
            logger.debug(
                "%s fallo %d/%d",
                dest.host, dest.consecutive_failures, PING_FAIL_THRESHOLD,
            )

            if dest.consecutive_failures >= PING_FAIL_THRESHOLD:
                # Transición UP → DOWN: abrir evento de caída
                origen = _determine_origen(dest, gateway_state, isp_hop_state)
                event_id = insert_evento_caida(dest.host, origen, now)
                dest.status = "DOWN"
                dest.current_event_id = event_id
                logger.warning(
                    "▼ %s caído (DOWN) — origen=%s evento=%d",
                    dest.host, origen, event_id,
                )

        # Si ya está DOWN, no hace nada (ya registrado)


async def connectivity_loop(
    gateway_ip: str,
    isp_hop_holder: MutableValue,
    window: SpeedtestWindow,
) -> None:
    """Loop principal de chequeo de conectividad. Corre indefinidamente."""

    # Inicializar estados
    gateway_state = DestinationState(host=gateway_ip, is_gateway=True)
    external_destinations = [
        DestinationState(host=target) for target in PING_TARGETS
    ]

    # ISP hop (puede ser None si no se detectó)
    isp_hop_ip = isp_hop_holder.get()
    isp_hop_state: Optional[DestinationState] = None
    if isp_hop_ip:
        isp_hop_state = DestinationState(host=isp_hop_ip, is_isp_hop=True)

    # Lista completa de destinos
    def _build_destinations():
        dests = [gateway_state]
        if isp_hop_state is not None:
            dests.append(isp_hop_state)
        dests.extend(external_destinations)
        return dests

    destinations = _build_destinations()

    # Reconciliar y cerrar eventos huérfanos de ejecuciones previas (ej. isp_hop viejo)
    now_utc = datetime.now(timezone.utc)
    active_hosts = set(d.host for d in destinations)
    all_open_events = get_all_open_eventos_caida()
    for open_event in all_open_events:
        if open_event["destino"] not in active_hosts:
            close_evento_caida(
                open_event["id"],
                now_utc,
                cierre_tipo="cambio_destino",
            )
            logger.info(
                "Evento huérfano id=%d para destino %s cerrado al iniciar (cambio_destino)",
                open_event["id"], open_event["destino"],
            )

    # Restaurar estado de los destinos activos actuales
    for dest in destinations:
        open_event_id = get_open_evento_caida(dest.host)
        if open_event_id is not None:
            dest.status = "DOWN"
            dest.current_event_id = open_event_id
            logger.info(
                "Recuperado evento abierto id=%d para %s — estado inicial ajustado a DOWN",
                open_event_id, dest.host,
            )


    logger.info(
        "Loop de conectividad iniciado — destinos: %s — intervalo: %ds",
        [d.host for d in destinations], PING_INTERVAL,
    )

    while True:
        # Verificar si isp_hop cambió (re-detección periódica)
        current_hop_ip = isp_hop_holder.get()
        if current_hop_ip != (isp_hop_state.host if isp_hop_state else None):
            # El hop cambió — manejar transición
            if isp_hop_state is not None and isp_hop_state.current_event_id is not None:
                # Cerrar evento viejo con cierre_tipo='cambio_destino'
                close_evento_caida(
                    isp_hop_state.current_event_id,
                    datetime.now(timezone.utc),
                    cierre_tipo="cambio_destino",
                )
                logger.info(
                    "ISP hop cambió de %s → %s — evento %d cerrado (cambio_destino)",
                    isp_hop_state.host, current_hop_ip, isp_hop_state.current_event_id,
                )

            if current_hop_ip:
                isp_hop_state = DestinationState(host=current_hop_ip, is_isp_hop=True)
                # Restaurar evento abierto si existe
                open_id = get_open_evento_caida(current_hop_ip)
                if open_id is not None:
                    isp_hop_state.status = "DOWN"
                    isp_hop_state.current_event_id = open_id
                logger.info("Nuevo ISP hop activo: %s", current_hop_ip)
            else:
                isp_hop_state = None
                logger.info("ISP hop no disponible, continuando sin isp_hop")

            destinations = _build_destinations()

        # Ping todos los destinos en paralelo
        results = await asyncio.gather(
            *[_ping_with_latency(dest.host) for dest in destinations]
        )

        # Procesar gateway primero (los demás necesitan su estado para origen)
        gw_success, _ = results[0]
        await _process_ping_result(gateway_state, gw_success, gateway_state, isp_hop_state)

        # Procesar isp_hop segundo (los externos necesitan su estado)
        start_idx = 1
        if isp_hop_state is not None:
            hop_success, _ = results[1]
            await _process_ping_result(isp_hop_state, hop_success, gateway_state, isp_hop_state)
            start_idx = 2

        # Procesar destinos externos
        for dest, (success, latency_ms) in zip(
            external_destinations, results[start_idx:]
        ):
            await _process_ping_result(dest, success, gateway_state, isp_hop_state)

            # Capturar latencia para bufferbloat (solo del target designado)
            if (
                dest.host == _BUFFERBLOAT_TARGET
                and window.is_active
                and latency_ms is not None
            ):
                await window.record_latency(latency_ms)

        await asyncio.sleep(PING_INTERVAL)
