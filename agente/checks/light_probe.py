"""Chequeo liviano de velocidad (Light Probe M-Lab NDT7).

Ejecuta descargas periódicas de 5 MB mediante el protocolo M-Lab NDT7 WebSocket (wss://)
consultando dinámicamente la Locate API (https://locate.measurementlab.net/v2/nearest/ndt/ndt7).

Mantiene manejo defensivo de errores:
- HTTP 429 (Rate Limit) o fallos de Locate API se loguean como WARNING y saltan la iteración
  sin marcar falsa degradación de red.
- Mide velocidad monohilo (~85-150 Mbps nominales) y evalúa contra baseline liviano propio.
"""

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
import websockets

from config import (
    LIGHT_PROBE_INTERVAL_SECONDS,
    LIGHT_PROBE_SIZE_MB,
)
from db import insert_medicion_probe_liviano
from checks.degradation import evaluate_degradation

logger = logging.getLogger(__name__)

LOCATE_API_URL = "https://locate.measurementlab.net/v2/nearest/ndt/ndt7"


class RateLimitError(Exception):
    """Excepción para HTTP 429 (Too Many Requests) de Locate API."""
    pass


class LocateAPIError(Exception):
    """Excepción para errores de respuesta de Locate API."""
    pass


_cached_server_info: tuple[str, str, float] | None = None  # (wss_url, machine, timestamp)


def _get_mlab_ndt7_download_url(timeout: int = 10, force_refresh: bool = False) -> tuple[str, str]:
    """Consulta la Locate API de M-Lab para obtener la URL WebSocket y el nombre del servidor NDT7.

    Returns:
        tuple (wss_url, machine)
    """
    global _cached_server_info
    now = time.time()
    if not force_refresh and _cached_server_info and (now - _cached_server_info[2] < 900):
        return _cached_server_info[0], _cached_server_info[1]

    req = urllib.request.Request(
        LOCATE_API_URL,
        headers={"User-Agent": "ControlInternetProbe/2.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 429:
                raise RateLimitError("Locate API retornó HTTP 429 Rate Limit")
            data = json.loads(response.read().decode())
            results = data.get("results", [])
            if not results:
                raise LocateAPIError("Locate API no retornó servidores disponibles")
            
            machine = results[0].get("machine", "desconocido")
            urls = results[0].get("urls", {})
            wss_url = urls.get("wss:///ndt/v7/download") or urls.get("ws:///ndt/v7/download")
            if not wss_url:
                raise LocateAPIError("URL NDT7 download no encontrada en respuesta de Locate API")
            
            _cached_server_info = (wss_url, machine, now)
            return wss_url, machine

    except urllib.error.HTTPError as e:
        if e.code == 429:
            # Si hay 429 pero tenemos cache (incluso expirado), reusarlo como fallback defensivo
            if _cached_server_info:
                logger.info("HTTP 429 en Locate API — reusando servidor M-Lab en caché: %s", _cached_server_info[1])
                return _cached_server_info[0], _cached_server_info[1]
            raise RateLimitError("Locate API retornó HTTP 429 Rate Limit") from e
        raise LocateAPIError(f"Error HTTP {e.code} en Locate API: {e.reason}") from e
    except Exception as e:
        if isinstance(e, (RateLimitError, LocateAPIError)):
            raise
        if _cached_server_info:
            logger.info("Fallo en Locate API (%s) — reusando servidor M-Lab en caché: %s", e, _cached_server_info[1])
            return _cached_server_info[0], _cached_server_info[1]
        raise LocateAPIError(f"Fallo consultando Locate API: {e}") from e


async def _run_ndt7_download(wss_url: str, target_bytes: int, timeout: int = 30) -> tuple[int, float]:
    """Descarga target_bytes desde el servidor WebSocket M-Lab NDT7.

    Returns:
        (bytes_received, elapsed_seconds)
    """
    start_time = time.monotonic()
    bytes_received = 0

    async with websockets.connect(
        wss_url, subprotocols=["net.measurementlab.ndt.v7"], close_timeout=5
    ) as ws:
        while True:
            # Si se excede el timeout general, salir
            if time.monotonic() - start_time > timeout:
                break

            try:
                message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                if isinstance(message, bytes):
                    bytes_received += len(message)
                elif isinstance(message, str):
                    # NDT7 envía mensajes de control JSON como texto
                    pass

                if bytes_received >= target_bytes:
                    break
            except asyncio.TimeoutError:
                break

    elapsed = time.monotonic() - start_time
    return bytes_received, elapsed


async def _run_cloudflare_download(target_bytes: int, timeout: int = 15) -> tuple[int, float]:
    """Fallback de descarga liviana utilizando Cloudflare Speedtest CDN (5MB)."""
    url = f"https://speed.cloudflare.com/__down?bytes={target_bytes}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    start_time = time.monotonic()

    def _do_download():
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    data = await asyncio.to_thread(_do_download)
    elapsed = time.monotonic() - start_time
    return len(data), elapsed


async def light_probe_loop() -> None:
    """Loop principal del test liviano (M-Lab NDT7 con fallback a Cloudflare CDN)."""
    target_bytes = int(LIGHT_PROBE_SIZE_MB * 1024 * 1024)
    logger.info(
        "Loop de test liviano iniciado — tamaño: %.1f MB — intervalo: %ds",
        LIGHT_PROBE_SIZE_MB, LIGHT_PROBE_INTERVAL_SECONDS,
    )

    while True:
        bytes_received = 0
        elapsed = 0.0
        servidor = "desconocido"

        try:
            # 1. Intentar M-Lab NDT7
            try:
                wss_url, machine = await asyncio.to_thread(_get_mlab_ndt7_download_url, 10)
                bytes_received, elapsed = await _run_ndt7_download(wss_url, target_bytes, 30)
                servidor = machine
            except Exception as e:
                # 2. Fallback defensivo a Cloudflare CDN si M-Lab da 429 o falla
                logger.info("Probe M-Lab no disponible (%s) — ejecutando fallback a Cloudflare CDN", e)
                bytes_received, elapsed = await _run_cloudflare_download(target_bytes, 15)
                servidor = "Cloudflare CDN"

            if bytes_received > 0 and elapsed > 0:
                mbps = (bytes_received * 8) / (1_000_000 * elapsed)
                tiempo_ms = elapsed * 1000.0

                insert_medicion_probe_liviano(mbps, tiempo_ms, servidor=servidor)
                evaluate_degradation(mbps, fuente="liviano")
                logger.info("Probe liviano OK: %.2f Mbps en %.0fms (servidor: %s)", mbps, tiempo_ms, servidor)
            else:
                logger.warning(
                    "Probe liviano no recibió datos suficientes: bytes=%d", bytes_received
                )

        except Exception as e:
            logger.warning("Probe liviano error general: %s", e)

        await asyncio.sleep(LIGHT_PROBE_INTERVAL_SECONDS)
