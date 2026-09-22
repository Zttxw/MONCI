"""Sensor Fast.com para el motor de detección V2.

Mide el rendimiento contra la infraestructura de medición de Netflix (Fast.com)
utilizando PycURL y la API oficial v2 de Fast.com en ejecuciones asíncronas desacopladas.

RESPONSABILIDAD ÚNICA:
- Mide throughput, duración en ms y bytes descargados.
- Retorna un objeto FastReading inmutable.
- NO conoce la FSM V2 ni decide cambios de estado.
"""

import asyncio
import io
import json
import logging
import re
import time
import urllib.request
from datetime import datetime, timezone
from typing import Optional

import pycurl
from config import FAST_TARGET_COUNT, FAST_TIMEOUT_SECONDS
from engine.models import FastReading
from sensors.base import BaseSensor

logger = logging.getLogger(__name__)


def _measure_fast_target(url: str, timeout: int = 10) -> dict:
    """Ejecuta una descarga individual vía PycURL contra un nodo CDN de Netflix."""
    buffer = io.BytesIO()
    c = pycurl.Curl()
    c.setopt(c.URL, url)
    c.setopt(c.WRITEDATA, buffer)
    c.setopt(c.FOLLOWLOCATION, True)
    c.setopt(c.TIMEOUT, timeout)
    c.setopt(c.USERAGENT, "Mozilla/5.0 (ControlInternetV2/FastSensor)")
    c.setopt(c.IPRESOLVE, pycurl.IPRESOLVE_V4)

    start = time.monotonic()
    try:
        c.perform()
        bytes_dl = c.getinfo(c.SIZE_DOWNLOAD_T)
        code = c.getinfo(c.RESPONSE_CODE)
        c.close()
        return {"status": code, "bytes": bytes_dl, "elapsed_s": (time.monotonic() - start), "error": None}
    except Exception as e:
        c.close()
        return {"status": 0, "bytes": 0, "elapsed_s": 0, "error": str(e)}


class FastSensor(BaseSensor):
    """Sensor pasivo V2 para la infraestructura de medición de Fast.com."""

    def __init__(self, target_count: int = FAST_TARGET_COUNT, timeout_seconds: int = FAST_TIMEOUT_SECONDS):
        super().__init__(name="Fast")
        self.target_count = target_count
        self.timeout_seconds = timeout_seconds

    async def measure(self) -> Optional[FastReading]:
        """Ejecuta la medición en Fast.com de forma asíncrona y retorna FastReading."""
        now = datetime.now(timezone.utc)
        start_wall = time.monotonic()

        try:
            # 1. Obtener token dinámico desde fast.com app JS
            req = urllib.request.Request("https://fast.com", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                html = resp.read().decode("utf-8")
                match = re.search(r'src=\"(/app-[a-f0-9]+\.js)\"', html)
                if not match:
                    return FastReading(
                        timestamp=now,
                        throughput_mbps=0.0,
                        duration_ms=0.0,
                        bytes_downloaded=0,
                        is_valid=False,
                        error="No se encontró app.js en Fast.com",
                    )
                js_url = "https://fast.com" + match.group(1)

            with urllib.request.urlopen(js_url, timeout=self.timeout_seconds) as js_resp:
                js_content = js_resp.read().decode("utf-8")
                token_match = re.search(r'token:\"([^\"]+)\"', js_content)
                if not token_match:
                    return FastReading(
                        timestamp=now,
                        throughput_mbps=0.0,
                        duration_ms=0.0,
                        bytes_downloaded=0,
                        is_valid=False,
                        error="No se encontró token de API en app.js",
                    )
                token = token_match.group(1)

            # 2. Consultar la API v2 de Fast.com para obtener URLs de descarga
            api_url = f"https://api.fast.com/netflix/speedtest/v2?https=true&token={token}&urlCount={self.target_count}"
            with urllib.request.urlopen(api_url, timeout=self.timeout_seconds) as api_resp:
                api_data = json.loads(api_resp.read().decode("utf-8"))
                targets = [t["url"] for t in api_data.get("targets", []) if "url" in t]

            if not targets:
                return FastReading(
                    timestamp=now,
                    throughput_mbps=0.0,
                    duration_ms=0.0,
                    bytes_downloaded=0,
                    is_valid=False,
                    error="Fast.com API no retornó nodos de descarga",
                )

            # 3. Descargar streams de targets concurrentes
            tasks = [
                asyncio.to_thread(_measure_fast_target, url, self.timeout_seconds)
                for url in targets[:self.target_count]
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            wall_duration_ms = (time.monotonic() - start_wall) * 1000.0

            valid = [r for r in results if isinstance(r, dict) and r.get("status") == 200]
            if not valid:
                return FastReading(
                    timestamp=now,
                    throughput_mbps=0.0,
                    duration_ms=round(wall_duration_ms, 1),
                    bytes_downloaded=0,
                    is_valid=False,
                    error="Todas las descargas de nodos Fast.com fallaron",
                )

            bytes_totales = sum(r["bytes"] for r in valid)
            transfer_wall_s = max(r["elapsed_s"] for r in valid)
            throughput = (bytes_totales * 8) / (1_000_000 * transfer_wall_s) if transfer_wall_s > 0 else 0.0

            reading = FastReading(
                timestamp=now,
                throughput_mbps=round(throughput, 2),
                duration_ms=round(wall_duration_ms, 1),
                bytes_downloaded=int(bytes_totales),
                is_valid=True,
                error=None,
                server_name="Netflix Open Connect",
            )

            logger.info(
                "FastSensor: throughput=%.2f Mbps en %.1f ms (%d bytes)",
                reading.throughput_mbps, reading.duration_ms, reading.bytes_downloaded
            )
            return reading

        except Exception as e:
            wall_duration_ms = (time.monotonic() - start_wall) * 1000.0
            logger.error("FastSensor: Error en medición: %s", e, exc_info=True)
            return FastReading(
                timestamp=now,
                throughput_mbps=0.0,
                duration_ms=round(wall_duration_ms, 1),
                bytes_downloaded=0,
                is_valid=False,
                error=str(e),
            )
