"""Modelos de datos del motor de detección V2.

Define los tipos inmutables para lecturas de sensores, estado de la FSM,
y registros de transición. Todos los enums heredan de str para facilitar
la serialización a JSON y SQLite.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums — FSM y Sensores
# ---------------------------------------------------------------------------

class FsmState(str, Enum):
    """Estados de la máquina de estados Mealy."""
    NORMAL = "NORMAL"
    SOSPECHA = "SOSPECHA"
    CONFIRMANDO = "CONFIRMANDO"
    EVENTO = "EVENTO"


class InputSymbol(str, Enum):
    """Símbolos de entrada aceptados por la FSM."""
    N = "n"     # Lectura normal / sana
    A = "a"     # Anomalía en sensor L1
    CO = "co"   # Caída total L0
    CD = "cd"   # Degradación L2 confirmada
    D = "d"     # Normalización observada


class OutputAction(str, Enum):
    """Acciones de salida Mealy generadas por las transiciones."""
    NADA = "NADA"
    REGISTRAR_SOSPECHA = "REGISTRAR_SOSPECHA"
    DISPARAR_L2 = "DISPARAR_L2"
    ABRIR_EVENTO_TOTAL = "ABRIR_EVENTO_TOTAL"
    ABRIR_EVENTO_DEGRADACION = "ABRIR_EVENTO_DEGRADACION"
    REGISTRAR_EVIDENCIA = "REGISTRAR_EVIDENCIA"
    REFRESCAR_EVENTO = "REFRESCAR_EVENTO"
    REFRESCAR_NORMAL = "REFRESCAR_NORMAL"
    CERRAR_EVENTO = "CERRAR_EVENTO"


# ---------------------------------------------------------------------------
# Dataclasses — Lecturas de Sensores V2
# ---------------------------------------------------------------------------

@dataclass
class L0Reading:
    """Lectura del Sensor L0 (Conectividad y Alcance)."""
    timestamp: datetime
    is_reachable: bool
    target: str
    latency_ms: Optional[float] = None
    packet_loss_pct: Optional[float] = None
    sub_checks: Optional[dict] = field(default_factory=dict)


@dataclass
class L1Reading:
    """Lectura del Sensor L1 (Probe Liviano de Rendimiento)."""
    timestamp: datetime
    throughput_mbps: float
    total_time_ms: float
    dns_ms: Optional[float] = None
    tcp_ms: Optional[float] = None
    tls_ms: Optional[float] = None
    ttfb_ms: Optional[float] = None
    transfer_ms: Optional[float] = None
    streams_used: int = 1
    baseline_mbps: Optional[float] = None
    bytes_downloaded: Optional[int] = None
    is_degraded: bool = False
    is_valid: bool = True



@dataclass
class L2Reading:
    """Lectura del Sensor L2 (Ookla Speedtest de Alta Precisión)."""
    timestamp: datetime
    download_mbps: float
    upload_mbps: float
    ping_ms: float
    loaded_latency_ms: Optional[float] = None
    baseline_mbps: Optional[float] = None
    server_id: Optional[int] = None
    server_name: Optional[str] = None


@dataclass
class FastReading:
    """Lectura del Sensor Fast.com (Infraestructura de Medición de Netflix)."""
    timestamp: datetime
    throughput_mbps: float
    duration_ms: float
    bytes_downloaded: int
    is_valid: bool = True
    error: Optional[str] = None
    server_name: Optional[str] = None
    baseline_mbps: Optional[float] = None
    is_degraded: bool = False


# ---------------------------------------------------------------------------
# Dataclasses — Estado FSM y Auditoría
# ---------------------------------------------------------------------------

@dataclass
class FsmSnapshot:
    """Captura de estado instantáneo de la FSM."""
    state: FsmState
    active_event_id: Optional[int] = None
    sospecha_start: Optional[datetime] = None
    last_check: Optional[datetime] = None


@dataclass
class TransitionRecord:
    """Registro inmutable de una transición en la FSM Mealy."""
    timestamp: datetime
    current_state: FsmState
    input_symbol: InputSymbol
    next_state: FsmState
    output_action: OutputAction
    is_reentry: bool = False
    readings_json: Optional[dict] = None
