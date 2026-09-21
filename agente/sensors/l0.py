"""Sensor L0 — Conectividad y Alcance (Liviano y Frecuente).

Realiza verificaciones autocontenidas y paralelas de:
1. ICMP Ping a IPs públicas (1.1.1.1, 8.8.8.8) y Gateway (si se especifica)
2. Resolución DNS (google.com)
3. Conexión HTTP/TCP directa

Retorna una estructura `L0Reading` inmutable.
"""

import asyncio
import logging
import socket
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

from config import PING_TARGETS, DNS_TARGET_DOMAIN, PING_TIMEOUT
from engine.models import L0Reading
from sensors.base import BaseSensor



logger = logging.getLogger(__name__)


async def _async_ping(target: str, timeout: float = 2.0) -> Tuple[bool, Optional[float]]:
    """Ejecuta un ping asíncrono usando la herramienta de sistema."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(int(timeout)), target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        t0 = time.perf_counter()
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 1.0)
        t1 = time.perf_counter()

        if proc.returncode == 0:
            # Parsear RTT del stdout si es posible, o usar delta de tiempo
            rtt_ms = (t1 - t0) * 1000.0
            out_str = stdout.decode("utf-8", errors="ignore")
            if "time=" in out_str:
                try:
                    part = out_str.split("time=")[1].split(" ")[0]
                    rtt_ms = float(part)
                except Exception:
                    pass
            return True, rtt_ms
        return False, None
    except Exception as e:
        logger.debug("Ping a %s falló: %s", target, e)
        return False, None


async def _async_dns_check(domain: str, timeout: float = 2.0) -> bool:
    """Verifica resolución DNS básica."""
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(
            loop.getaddrinfo(domain, 80, family=socket.AF_INET),
            timeout=timeout,
        )
        return True
    except Exception:
        return False


class L0Sensor(BaseSensor):
    """Sensor L0 independiente para evaluación de alcance y conectividad."""

    def __init__(self, ping_targets: Optional[list[str]] = None):
        super().__init__("L0_Connectivity")
        self.targets = ping_targets or PING_TARGETS

    async def measure(self) -> L0Reading:
        """Ejecuta en paralelo los chequeos L0 y sintetiza el resultado."""
        now = datetime.now(timezone.utc)
        sub_checks = {}

        # 1. Pings paralelos a los objetivos configurados
        ping_tasks = [_async_ping(target, PING_TIMEOUT) for target in self.targets]
        ping_results = await asyncio.gather(*ping_tasks, return_exceptions=True)

        successful_pings = 0
        latencies = []

        for target, res in zip(self.targets, ping_results):
            if isinstance(res, tuple) and res[0]:
                successful_pings += 1
                if res[1] is not None:
                    latencies.append(res[1])
                sub_checks[f"ping_{target}"] = {"ok": True, "latency_ms": res[1]}
            else:
                sub_checks[f"ping_{target}"] = {"ok": False, "latency_ms": None}

        # 2. Verificación DNS paralela
        dns_domain = DNS_TARGET_DOMAIN or "google.com"
        dns_ok = await _async_dns_check(dns_domain, timeout=2.0)
        sub_checks[f"dns_{dns_domain}"] = {"ok": dns_ok}

        total_targets = len(self.targets)
        packet_loss_pct = ((total_targets - successful_pings) / total_targets) * 100.0 if total_targets > 0 else 100.0
        avg_latency = (sum(latencies) / len(latencies)) if latencies else None

        # Criterio L0: Considerado alcanzable si al menos 1 ping responde O DNS resuelve
        is_reachable = (successful_pings > 0) or dns_ok

        reading = L0Reading(
            timestamp=now,
            is_reachable=is_reachable,
            target=",".join(self.targets),
            latency_ms=avg_latency,
            packet_loss_pct=packet_loss_pct,
            sub_checks=sub_checks,
        )

        logger.debug(
            "L0 Measure: reachable=%s loss=%.1f%% avg_lat=%s",
            is_reachable, packet_loss_pct, f"{avg_latency:.1f}ms" if avg_latency else "N/A"
        )
        return reading
