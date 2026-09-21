"""Chequeo liviano de velocidad con descomposición de tiempos y 3 streams paralelos PycURL.

Métrica de Fase 0:
- Utiliza PycURL para medir con precisión de microsegundos:
  dns_ms, tcp_connect_ms, tls_ms, ttfb_ms, transfer_ms, mbps_throughput, mbps_aproximado.
- Ejecuta 3 streams TCP concurrentes (LIGHT_PROBE_STREAMS = 3) para medir throughput agregado.
- Registra muestra_valida = 0 durante el cooldown post-speedtest (60s).
- Fallback defensivo de PycURL a NDT7 WebSocket / Cloudflare CDN.
"""

import asyncio
import io
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional

import pycurl
import websockets

from config import (
    LIGHT_PROBE_COOLDOWN_SECONDS,
    LIGHT_PROBE_INTERVAL_SECONDS,
    LIGHT_PROBE_SIZE_MB,
    LIGHT_PROBE_STREAMS,
    LIGHT_PROBE_URL,
)
from db import insert_medicion_probe_liviano
from checks.degradation import evaluate_degradation
from checks.shared import SpeedtestWindow

logger = logging.getLogger(__name__)

LOCATE_API_URL = "https://locate.measurementlab.net/v2/nearest/ndt/ndt7"


class RateLimitError(Exception):
    """Excepción para HTTP 429 (Too Many Requests) de Locate API."""
    pass


class LocateAPIError(Exception):
    """Excepción para errores de respuesta de Locate API."""
    pass


_cached_server_info: tuple[str, str, float] | None = None  # (wss_url, machine, timestamp)


def _measure_pycurl_stream(url: str, timeout: int = 15) -> dict:
    """Ejecuta una descarga individual con PycURL y retorna métricas descompuestas."""
    buffer = io.BytesIO()
    c = pycurl.Curl()
    c.setopt(c.URL, url)
    c.setopt(c.WRITEDATA, buffer)
    c.setopt(c.FOLLOWLOCATION, True)
    c.setopt(c.USERAGENT, "ControlInternetProbe/2.0")
    c.setopt(c.TIMEOUT, timeout)

    c.perform()

    namelookup = c.getinfo(c.NAMELOOKUP_TIME)
    connect = c.getinfo(c.CONNECT_TIME)
    appconnect = c.getinfo(c.APPCONNECT_TIME)
    starttransfer = c.getinfo(c.STARTTRANSFER_TIME)
    total = c.getinfo(c.TOTAL_TIME)
    bytes_downloaded = c.getinfo(c.SIZE_DOWNLOAD_T)
    response_code = c.getinfo(c.RESPONSE_CODE)

    c.close()

    dns_ms = namelookup * 1000.0
    tcp_connect_ms = (connect - namelookup) * 1000.0 if connect > namelookup else 0.0
    tls_ms = (appconnect - connect) * 1000.0 if (appconnect > 0 and appconnect > connect) else 0.0

    base_pre_ttfb = appconnect if appconnect > 0 else connect
    ttfb_ms = (starttransfer - base_pre_ttfb) * 1000.0 if starttransfer > base_pre_ttfb else 0.0
    transfer_ms = (total - starttransfer) * 1000.0 if total > starttransfer else 0.0

    mbps_throughput = (bytes_downloaded * 8) / (1_000_000 * (transfer_ms / 1000.0)) if transfer_ms > 0 else 0.0
    mbps_aproximado = (bytes_downloaded * 8) / (1_000_000 * total) if total > 0 else 0.0

    return {
        "status": response_code,
        "bytes": bytes_downloaded,
        "dns_ms": dns_ms,
        "tcp_connect_ms": tcp_connect_ms,
        "tls_ms": tls_ms,
        "ttfb_ms": ttfb_ms,
        "transfer_ms": transfer_ms,
        "total_ms": total * 1000.0,
        "mbps_throughput": mbps_throughput,
        "mbps_aproximado": mbps_aproximado,
    }


async def _run_multi_stream_pycurl(url: str, streams: int = 3, timeout: int = 15) -> dict:
    """Ejecuta streams concurrentes con PycURL y calcula throughput agregado y peor caso de fases."""
    start_wall = time.monotonic()
    tasks = [
        asyncio.to_thread(_measure_pycurl_stream, url, timeout)
        for _ in range(streams)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    wall_duration_ms = (time.monotonic() - start_wall) * 1000.0

    valid_results = [r for r in results if isinstance(r, dict) and r.get("status") == 200]
    if not valid_results:
        raise RuntimeError("Todos los streams PycURL fallaron")

    bytes_totales = sum(r["bytes"] for r in valid_results)
    transfer_wall_ms = max(r["transfer_ms"] for r in valid_results)

    # Throughput agregado = bytes totales / tiempo de pared de transferencia
    mbps_throughput = (bytes_totales * 8) / (1_000_000 * (transfer_wall_ms / 1000.0)) if transfer_wall_ms > 0 else 0.0
    mbps_aproximado = (bytes_totales * 8) / (1_000_000 * (wall_duration_ms / 1000.0)) if wall_duration_ms > 0 else 0.0

    # Peor caso (máximo) para detectar degradaciones en cada fase de conexión
    dns_ms = max(r["dns_ms"] for r in valid_results)
    tcp_connect_ms = max(r["tcp_connect_ms"] for r in valid_results)
    tls_ms = max(r["tls_ms"] for r in valid_results)
    ttfb_ms = max(r["ttfb_ms"] for r in valid_results)

    return {
        "bytes_totales": bytes_totales,
        "dns_ms": dns_ms,
        "tcp_connect_ms": tcp_connect_ms,
        "tls_ms": tls_ms,
        "ttfb_ms": ttfb_ms,
        "transfer_ms": transfer_wall_ms,
        "total_ms": wall_duration_ms,
        "mbps_throughput": mbps_throughput,
        "mbps_aproximado": mbps_aproximado,
        "streams_usados": len(valid_results),
    }


def _get_mlab_ndt7_download_url(timeout: int = 10, force_refresh: bool = False) -> tuple[str, str]:
    """Consulta Locate API de M-Lab."""
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
            if _cached_server_info:
                return _cached_server_info[0], _cached_server_info[1]
            raise RateLimitError("Locate API retornó HTTP 429 Rate Limit") from e
        raise LocateAPIError(f"Error HTTP {e.code} en Locate API: {e.reason}") from e
    except Exception as e:
        if isinstance(e, (RateLimitError, LocateAPIError)):
            raise
        if _cached_server_info:
            return _cached_server_info[0], _cached_server_info[1]
        raise LocateAPIError(f"Fallo consultando Locate API: {e}") from e


async def _run_ndt7_download(wss_url: str, target_bytes: int, timeout: int = 30) -> tuple[int, float]:
    """Descarga target_bytes desde el servidor WebSocket M-Lab NDT7."""
    start_time = time.monotonic()
    bytes_received = 0

    async with websockets.connect(
        wss_url, subprotocols=["net.measurementlab.ndt.v7"], close_timeout=5
    ) as ws:
        while True:
            if time.monotonic() - start_time > timeout:
                break

            try:
                message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                if isinstance(message, bytes):
                    bytes_received += len(message)
                if bytes_received >= target_bytes:
                    break
            except asyncio.TimeoutError:
                break

    elapsed = time.monotonic() - start_time
    return bytes_received, elapsed


async def light_probe_loop(window: Optional[SpeedtestWindow] = None) -> None:
    """Loop principal del probe liviano (PycURL multi-stream HTTP con fallback WebSocket NDT7)."""
    target_bytes = int(LIGHT_PROBE_SIZE_MB * 1024 * 1024)
    logger.info(
        "Loop de test liviano iniciado — tamaño: %.1f MB — streams: %d — intervalo: %ds",
        LIGHT_PROBE_SIZE_MB, LIGHT_PROBE_STREAMS, LIGHT_PROBE_INTERVAL_SECONDS,
    )

    while True:
        servidor = "Cloudflare CDN"
        muestra_valida = True

        # Verificar cooldown post-speedtest
        if window and window.is_in_cooldown:
            muestra_valida = False
            logger.info("Probe liviano ejecutándose en ventana de cooldown post-speedtest (muestra_valida=False)")

        try:
            # 1. Intentar descargas paralelas HTTP PycURL con descomposición de tiempos
            try:
                res = await _run_multi_stream_pycurl(LIGHT_PROBE_URL, streams=LIGHT_PROBE_STREAMS, timeout=15)
                mbps_throughput = res["mbps_throughput"]
                mbps_aprox = res["mbps_aproximado"]
                tiempo_total_ms = res["total_ms"]
                servidor = f"Cloudflare CDN ({res['streams_usados']} streams)"

                if res["streams_usados"] < LIGHT_PROBE_STREAMS:
                    logger.warning(
                        "Fallo parcial de streams en probe liviano: %d/%d streams respondieron correctamente",
                        res["streams_usados"],
                        LIGHT_PROBE_STREAMS,
                    )

                insert_medicion_probe_liviano(
                    mbps_aproximado=mbps_aprox,
                    tiempo_respuesta_ms=tiempo_total_ms,
                    servidor=servidor,
                    dns_ms=res["dns_ms"],
                    tcp_connect_ms=res["tcp_connect_ms"],
                    tls_ms=res["tls_ms"],
                    ttfb_ms=res["ttfb_ms"],
                    transfer_ms=res["transfer_ms"],
                    mbps_throughput=mbps_throughput,
                    streams_usados=res["streams_usados"],
                    muestra_valida=muestra_valida,
                )

                if muestra_valida:
                    evaluate_degradation(mbps_throughput, fuente="liviano")

                logger.info(
                    "Probe liviano OK: throughput=%.2f Mbps (aprox=%.2f Mbps) DNS=%.1fms TCP=%.1fms TLS=%.1fms TTFB=%.1fms Transfer=%.1fms",
                    mbps_throughput, mbps_aprox, res["dns_ms"], res["tcp_connect_ms"], res["tls_ms"], res["ttfb_ms"], res["transfer_ms"]
                )

            except Exception as e_pycurl:
                logger.info("PycURL multi-stream no disponible (%s) — intentando M-Lab NDT7 WebSocket", e_pycurl)
                # Fallback a M-Lab NDT7 WebSocket
                wss_url, machine = await asyncio.to_thread(_get_mlab_ndt7_download_url, 10)
                bytes_rec, elapsed = await _run_ndt7_download(wss_url, target_bytes, 30)
                servidor = machine

                if bytes_rec > 0 and elapsed > 0:
                    mbps_aprox = (bytes_rec * 8) / (1_000_000 * elapsed)
                    tiempo_ms = elapsed * 1000.0

                    insert_medicion_probe_liviano(
                        mbps_aproximado=mbps_aprox,
                        tiempo_respuesta_ms=tiempo_ms,
                        servidor=servidor,
                        mbps_throughput=None,
                        streams_usados=1,
                        muestra_valida=muestra_valida,
                    )
                    if muestra_valida:
                        evaluate_degradation(mbps_aprox, fuente="liviano")

        except Exception as e:
            logger.warning("Probe liviano error general: %s", e)

        await asyncio.sleep(LIGHT_PROBE_INTERVAL_SECONDS)

