"""Pydantic models para respuestas de la API."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class EstadoDestino(BaseModel):
    destino: str
    estado: str          # "UP" | "DOWN"
    desde: Optional[str] = None   # timestamp del último cambio


class EventoCaida(BaseModel):
    id: int
    destino: str
    origen: str          # "red_local" | "isp_primer_salto" | "isp_general"
    inicio: str
    fin: Optional[str] = None
    duracion_segundos: Optional[int] = None
    cierre_tipo: Optional[str] = None   # "recuperacion" | "cambio_destino" | None
    senal_wifi: Optional[int] = None    # RSSI dBm, siempre NULL en deploy por cable


class EventoDNS(BaseModel):
    id: int
    dominio: str
    servidor_dns: str
    inicio: str
    fin: Optional[str] = None
    duracion_segundos: Optional[int] = None


class MedicionVelocidad(BaseModel):
    id: int
    timestamp: str
    descarga_mbps: Optional[float] = None
    subida_mbps: Optional[float] = None
    ping_ms: Optional[float] = None
    latencia_bajo_carga_ms: Optional[float] = None


class UsoBandwidth(BaseModel):
    id: int
    timestamp: str
    bytes_in: Optional[int] = None
    bytes_out: Optional[int] = None


class EstadoDNS(BaseModel):
    """Estado actual de la resolución DNS."""
    dominio: str
    servidor_dns: str
    estado: str          # "UP" | "DOWN"
    desde: Optional[str] = None


class MedicionProbeLiviano(BaseModel):
    id: int
    timestamp: str
    mbps_aproximado: float
    tiempo_respuesta_ms: float
    servidor: Optional[str] = None
    dns_ms: Optional[float] = None
    tcp_connect_ms: Optional[float] = None
    tls_ms: Optional[float] = None
    ttfb_ms: Optional[float] = None
    transfer_ms: Optional[float] = None
    mbps_throughput: Optional[float] = None
    latencia_ms: Optional[float] = None
    streams_usados: Optional[int] = 1
    muestra_valida: Optional[int] = 1


class EventoDegradacion(BaseModel):
    id: int
    severidad: str          # "degradada" | "critica"
    fuente: str             # "oficial" | "liviano"
    baseline_mbps: float
    velocidad_mbps: float
    inicio: str
    fin: Optional[str] = None
    duracion_segundos: Optional[int] = None


class ResumenEstado(BaseModel):
    """Respuesta del endpoint /api/status."""
    destinos: list[EstadoDestino]
    ultima_velocidad: Optional[MedicionVelocidad] = None
    ultimo_probe_liviano: Optional[MedicionProbeLiviano] = None
    estado_dns: Optional[EstadoDNS] = None
    gateway_ip: Optional[str] = None
    isp_hop_ip: Optional[str] = None
    probe_liviano_count: int = 0
    probe_liviano_baseline: Optional[float] = None


