"""Sensor L1 — Probe Liviano de Rendimiento (Frecuencia Media).

Mide throughput de descarga usando descargas concurrentes PycURL con descomposición
de tiempos microsegundo (dns_ms, tcp_ms, tls_ms, ttfb_ms, transfer_ms).

Calcula `is_degraded` comparando el throughput medido contra el baseline histórico.
Retorna una estructura `L1Reading` inmutable.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from config import (
    LIGHT_PROBE_URL,
    LIGHT_PROBE_STREAMS,
    DEGRADATION_THRESHOLD_PERCENT,
)
from db import get_baseline_mbps_liviano
from engine.models import L1Reading
from sensors.base import BaseSensor

# Importar lógica PycURL de alta precisión existente
from checks.light_probe import _run_multi_stream_pycurl, _get_mlab_ndt7_download_url, _run_ndt7_download


logger = logging.getLogger(__name__)


class L1Sensor(BaseSensor):
    """Sensor L1 independiente para evaluar throughput liviano de red."""

    def __init__(self, streams: int = LIGHT_PROBE_STREAMS, url: str = LIGHT_PROBE_URL):
        super().__init__("L1_LightProbe")
        self.streams = streams
        self.url = url

    async def measure(self, is_cooldown_active: bool = False) -> L1Reading:
        """Ejecuta una medición de throughput y determina degradación."""
        now = datetime.now(timezone.utc)
        is_valid = not is_cooldown_active  # Si estamos en cooldown pos-L2, la muestra no es válida para FSM

        try:
            # 1. Intentar PycURL multi-stream
            res = await _run_multi_stream_pycurl(self.url, streams=self.streams, timeout=15)
            throughput = res["mbps_throughput"]
            total_time = res["total_ms"]
            dns_ms = res.get("dns_ms")
            tcp_ms = res.get("tcp_connect_ms")
            tls_ms = res.get("tls_ms")
            ttfb_ms = res.get("ttfb_ms")
            transfer_ms = res.get("transfer_ms")
            streams_used = res.get("streams_usados", self.streams)
            bytes_totales = res.get("bytes_totales", 0)

        except Exception as e:
            logger.info("L1 PycURL no disponible (%s) — ejecutando fallback NDT7", e)
            try:
                wss_url, _ = await asyncio.to_thread(_get_mlab_ndt7_download_url, 10)
                bytes_rec, elapsed = await _run_ndt7_download(wss_url, 5_000_000, 20)
                bytes_totales = bytes_rec
                if elapsed > 0:
                    throughput = (bytes_rec * 8) / (1_000_000 * elapsed)
                    total_time = elapsed * 1000.0
                else:
                    throughput, total_time = 0.0, 0.0
                dns_ms, tcp_ms, tls_ms, ttfb_ms, transfer_ms = None, None, None, None, total_time
                streams_used = 1
            except Exception as e_fallback:
                logger.error("L1 Sensor falló completamente: %s", e_fallback)
                return L1Reading(
                    timestamp=now,
                    throughput_mbps=0.0,
                    total_time_ms=0.0,
                    bytes_downloaded=0,
                    is_degraded=False,
                    is_valid=False,
                )

        # Obtener baseline para comparar degradación
        baseline = get_baseline_mbps_liviano(limit=20, min_count=5)
        is_degraded = False

        if baseline and baseline > 0 and is_valid:
            # Si el throughput cayó más del DEGRADATION_THRESHOLD_PERCENT (e.g. < 50% del baseline)
            umbral = baseline * (DEGRADATION_THRESHOLD_PERCENT / 100.0)
            if throughput < umbral:
                is_degraded = True

        reading = L1Reading(
            timestamp=now,
            throughput_mbps=throughput,
            total_time_ms=total_time,
            dns_ms=dns_ms,
            tcp_ms=tcp_ms,
            tls_ms=tls_ms,
            ttfb_ms=ttfb_ms,
            transfer_ms=transfer_ms,
            streams_used=streams_used,
            baseline_mbps=baseline,
            bytes_downloaded=bytes_totales,
            is_degraded=is_degraded,
            is_valid=is_valid,
        )

        logger.debug(
            "L1 Measure: throughput=%.2f Mbps baseline=%s degraded=%s valid=%s",
            throughput, f"{baseline:.2f} Mbps" if baseline else "N/A", is_degraded, is_valid
        )
        return reading
