"""Chequeo de resolución DNS — máquina de estados UP/DOWN.

Reglas de diseño (ARCHITECTURE.md):
- Resolver un dominio conocido (google.com) contra un servidor DNS
  específico (8.8.8.8) cada 5 segundos.
- Máquina de estados UP/DOWN con umbral de 2 fallos consecutivos
  (mismo patrón que conectividad).
- Eventos registrados en tabla eventos_dns, separados de eventos_caida.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import dns.resolver

from config import DNS_CHECK_INTERVAL, DNS_FAIL_THRESHOLD, DNS_TARGET_DOMAIN, DNS_SERVER
from db import insert_evento_dns, close_evento_dns, get_open_evento_dns

logger = logging.getLogger(__name__)


@dataclass
class DNSState:
    """Estado de la resolución DNS.

    Mismo patrón que DestinationState en connectivity.py:
    arranca UP, 2 fallos consecutivos → DOWN.
    """

    dominio: str
    servidor_dns: str
    status: str = "UP"
    consecutive_failures: int = 0
    current_event_id: Optional[int] = None


async def _resolve_dns(
    dominio: str,
    servidor_dns: str,
    timeout: float = 3.0,
) -> tuple[bool, Optional[float]]:
    """Intenta resolver un dominio contra un servidor DNS específico.

    Retorna (success, response_time_ms).
    Ejecuta en thread para no bloquear el event loop (dnspython es síncrono).
    """

    def _do_resolve() -> tuple[bool, Optional[float]]:
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [servidor_dns]
        resolver.lifetime = timeout

        start = time.monotonic()
        try:
            resolver.resolve(dominio, "A")
            elapsed = (time.monotonic() - start) * 1000
            return True, round(elapsed, 3)
        except Exception:
            return False, None

    return await asyncio.to_thread(_do_resolve)


async def _process_dns_result(
    state: DNSState,
    success: bool,
) -> None:
    """Procesa el resultado de una resolución DNS y ejecuta transiciones."""
    now = datetime.now(timezone.utc)

    if success:
        if state.status == "DOWN":
            # Transición DOWN → UP: cerrar evento DNS
            if state.current_event_id is not None:
                close_evento_dns(state.current_event_id, now)
                logger.info(
                    "▲ DNS recuperado (UP) — %s@%s — evento %d cerrado",
                    state.dominio, state.servidor_dns, state.current_event_id,
                )
            state.status = "UP"
            state.current_event_id = None

        state.consecutive_failures = 0

    else:
        if state.status == "UP":
            state.consecutive_failures += 1
            logger.debug(
                "DNS fallo %d/%d — %s@%s",
                state.consecutive_failures, DNS_FAIL_THRESHOLD,
                state.dominio, state.servidor_dns,
            )

            if state.consecutive_failures >= DNS_FAIL_THRESHOLD:
                # Transición UP → DOWN: abrir evento DNS
                event_id = insert_evento_dns(
                    state.dominio, state.servidor_dns, now,
                )
                state.status = "DOWN"
                state.current_event_id = event_id
                logger.warning(
                    "▼ DNS caído (DOWN) — %s@%s — evento=%d",
                    state.dominio, state.servidor_dns, event_id,
                )


async def dns_loop() -> None:
    """Loop principal de chequeo DNS. Corre indefinidamente."""
    state = DNSState(
        dominio=DNS_TARGET_DOMAIN,
        servidor_dns=DNS_SERVER,
    )

    # Restaurar evento abierto si existe
    open_event_id = get_open_evento_dns(state.dominio, state.servidor_dns)
    if open_event_id is not None:
        state.status = "DOWN"
        state.current_event_id = open_event_id
        logger.info(
            "Recuperado evento DNS abierto id=%d para %s@%s — estado inicial ajustado a DOWN",
            open_event_id, state.dominio, state.servidor_dns,
        )

    logger.info(
        "Loop DNS iniciado — dominio: %s servidor: %s intervalo: %ds",
        DNS_TARGET_DOMAIN, DNS_SERVER, DNS_CHECK_INTERVAL,
    )

    while True:
        try:
            success, response_ms = await _resolve_dns(
                state.dominio, state.servidor_dns,
            )
            if success:
                logger.debug("DNS OK — %s@%s — %.1fms", state.dominio, state.servidor_dns, response_ms)
            await _process_dns_result(state, success)
        except Exception as e:
            logger.error("Error inesperado en check DNS: %s", e)

        await asyncio.sleep(DNS_CHECK_INTERVAL)
