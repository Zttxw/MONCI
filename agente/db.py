"""Inicialización y helpers de SQLite para el agente.

Usa WAL mode para permitir lectura concurrente desde el dashboard
mientras el agente escribe.
"""

import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from config import DB_PATH


logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS eventos_caida (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    destino TEXT NOT NULL,
    origen TEXT NOT NULL,
    inicio DATETIME NOT NULL,
    fin DATETIME,
    duracion_segundos INTEGER,
    cierre_tipo TEXT,
    senal_wifi INTEGER
);

CREATE TABLE IF NOT EXISTS eventos_dns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dominio TEXT NOT NULL,
    servidor_dns TEXT NOT NULL,
    inicio DATETIME NOT NULL,
    fin DATETIME,
    duracion_segundos INTEGER
);

CREATE TABLE IF NOT EXISTS mediciones_velocidad (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    descarga_mbps REAL,
    subida_mbps REAL,
    ping_ms REAL,
    latencia_bajo_carga_ms REAL
);

CREATE TABLE IF NOT EXISTS mediciones_probe_liviano (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    mbps_aproximado REAL NOT NULL,
    tiempo_respuesta_ms REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS eventos_degradacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    severidad TEXT NOT NULL,
    fuente TEXT NOT NULL,
    baseline_mbps REAL NOT NULL,
    velocidad_mbps REAL NOT NULL,
    inicio DATETIME NOT NULL,
    fin DATETIME,
    duracion_segundos INTEGER
);

CREATE TABLE IF NOT EXISTS uso_ancho_banda (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    bytes_in INTEGER,
    bytes_out INTEGER
);

CREATE TABLE IF NOT EXISTS metadata (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

-- ===========================================================================
-- TABLAS ARQUITECTURA V2 — Detección con FSM Mealy y Sensores L0/L1/L2
-- ===========================================================================

CREATE TABLE IF NOT EXISTS v2_l0_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    is_reachable BOOLEAN NOT NULL,
    target TEXT NOT NULL,
    latency_ms REAL,
    packet_loss_pct REAL,
    sub_checks TEXT
);

CREATE TABLE IF NOT EXISTS v2_l1_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    throughput_mbps REAL NOT NULL,
    total_time_ms REAL NOT NULL,
    dns_ms REAL,
    tcp_ms REAL,
    tls_ms REAL,
    ttfb_ms REAL,
    transfer_ms REAL,
    streams_used INTEGER,
    baseline_mbps REAL,
    is_degraded BOOLEAN NOT NULL,
    is_valid BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_l2_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    download_mbps REAL NOT NULL,
    upload_mbps REAL NOT NULL,
    ping_ms REAL NOT NULL,
    loaded_latency_ms REAL,
    baseline_mbps REAL,
    server_id INTEGER,
    server_name TEXT
);

CREATE TABLE IF NOT EXISTS v2_fsm_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    current_state TEXT NOT NULL,
    input_symbol TEXT NOT NULL,
    next_state TEXT NOT NULL,
    output_action TEXT NOT NULL,
    is_reentry BOOLEAN NOT NULL DEFAULT 0,
    readings_json TEXT
);

CREATE TABLE IF NOT EXISTS v2_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    state_origin TEXT NOT NULL,
    start_time DATETIME NOT NULL,
    end_time DATETIME,
    duration_seconds INTEGER,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    l0_status TEXT,
    l1_throughput REAL,
    l2_download REAL,
    diagnosis_code TEXT,
    diagnosis_detail TEXT,
    evidence_json TEXT
);
"""


# ---------------------------------------------------------------------------
# Migraciones idempotentes para bases existentes
# ---------------------------------------------------------------------------

_MIGRATIONS = [
    "ALTER TABLE eventos_caida ADD COLUMN cierre_tipo TEXT",
    "ALTER TABLE eventos_caida ADD COLUMN senal_wifi INTEGER",
    "ALTER TABLE mediciones_velocidad ADD COLUMN latencia_bajo_carga_ms REAL",
    "ALTER TABLE eventos_degradacion ADD COLUMN fuente TEXT",
    "ALTER TABLE eventos_degradacion ADD COLUMN severidad TEXT",
    "ALTER TABLE eventos_degradacion ADD COLUMN duracion_segundos INTEGER",
    "ALTER TABLE mediciones_probe_liviano ADD COLUMN servidor TEXT",
    "ALTER TABLE mediciones_probe_liviano ADD COLUMN dns_ms REAL",
    "ALTER TABLE mediciones_probe_liviano ADD COLUMN tcp_connect_ms REAL",
    "ALTER TABLE mediciones_probe_liviano ADD COLUMN tls_ms REAL",
    "ALTER TABLE mediciones_probe_liviano ADD COLUMN ttfb_ms REAL",
    "ALTER TABLE mediciones_probe_liviano ADD COLUMN transfer_ms REAL",
    "ALTER TABLE mediciones_probe_liviano ADD COLUMN mbps_throughput REAL",
    "ALTER TABLE mediciones_probe_liviano ADD COLUMN latencia_ms REAL",
    "ALTER TABLE mediciones_probe_liviano ADD COLUMN streams_usados INTEGER DEFAULT 1",
    "ALTER TABLE mediciones_probe_liviano ADD COLUMN muestra_valida INTEGER DEFAULT 1",
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Ejecuta ALTER TABLE idempotentes para bases pre-existentes."""
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
            logger.info("Migración aplicada: %s", sql)
        except sqlite3.OperationalError:
            # "duplicate column name" — la columna ya existe, ignorar
            pass


def init_db() -> None:
    """Crea las tablas si no existen, aplica migraciones y habilita WAL mode."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _run_migrations(conn)
        conn.commit()
        logger.info("Base de datos inicializada en %s", DB_PATH)
    finally:
        conn.close()



@contextmanager
def get_connection():
    """Context manager que entrega una conexión SQLite con WAL y row_factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers — eventos de caída
# ---------------------------------------------------------------------------

def insert_evento_caida(destino: str, origen: str, inicio: datetime) -> int:
    """Abre un nuevo evento de caída. Retorna el ID insertado."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO eventos_caida (destino, origen, inicio) VALUES (?, ?, ?)",
            (destino, origen, inicio.isoformat()),
        )
        event_id = cursor.lastrowid
        logger.info(
            "Evento de caída abierto id=%d destino=%s origen=%s inicio=%s",
            event_id, destino, origen, inicio.isoformat(),
        )
        return event_id


def close_evento_caida(
    event_id: int,
    fin: datetime,
    cierre_tipo: str = "recuperacion",
) -> None:
    """Cierra un evento de caída existente, calculando la duración.

    cierre_tipo: 'recuperacion' (ping volvió a responder) o
                 'cambio_destino' (rotación de isp_hop).
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT inicio FROM eventos_caida WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            logger.warning("Evento id=%d no encontrado al intentar cerrar", event_id)
            return

        inicio = datetime.fromisoformat(row["inicio"])
        duracion = int((fin - inicio).total_seconds())
        conn.execute(
            "UPDATE eventos_caida SET fin = ?, duracion_segundos = ?, cierre_tipo = ? WHERE id = ?",
            (fin.isoformat(), duracion, cierre_tipo, event_id),
        )
        logger.info(
            "Evento de caída cerrado id=%d fin=%s duración=%ds cierre_tipo=%s",
            event_id, fin.isoformat(), duracion, cierre_tipo,
        )


def get_open_evento_caida(destino: str) -> Optional[int]:
    """Retorna el id del evento abierto (fin IS NULL) para un destino, si existe."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM eventos_caida WHERE destino = ? AND fin IS NULL ORDER BY inicio DESC LIMIT 1",
            (destino,),
        ).fetchone()
        if row:
            return row["id"]
    return None


def get_all_open_eventos_caida() -> list[dict]:
    """Retorna todos los eventos de caída abiertos (fin IS NULL) en la base de datos."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, destino, origen, inicio FROM eventos_caida WHERE fin IS NULL"
        ).fetchall()
        return [dict(r) for r in rows]



# ---------------------------------------------------------------------------
# Helpers — eventos DNS
# ---------------------------------------------------------------------------

def insert_evento_dns(dominio: str, servidor_dns: str, inicio: datetime) -> int:
    """Abre un nuevo evento de falla DNS. Retorna el ID insertado."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO eventos_dns (dominio, servidor_dns, inicio) VALUES (?, ?, ?)",
            (dominio, servidor_dns, inicio.isoformat()),
        )
        event_id = cursor.lastrowid
        logger.info(
            "Evento DNS abierto id=%d dominio=%s servidor=%s inicio=%s",
            event_id, dominio, servidor_dns, inicio.isoformat(),
        )
        return event_id


def close_evento_dns(event_id: int, fin: datetime) -> None:
    """Cierra un evento de falla DNS, calculando la duración."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT inicio FROM eventos_dns WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            logger.warning("Evento DNS id=%d no encontrado al intentar cerrar", event_id)
            return

        inicio = datetime.fromisoformat(row["inicio"])
        duracion = int((fin - inicio).total_seconds())
        conn.execute(
            "UPDATE eventos_dns SET fin = ?, duracion_segundos = ? WHERE id = ?",
            (fin.isoformat(), duracion, event_id),
        )
        logger.info(
            "Evento DNS cerrado id=%d fin=%s duración=%ds",
            event_id, fin.isoformat(), duracion,
        )


def get_open_evento_dns(dominio: str, servidor_dns: str) -> Optional[int]:
    """Retorna el id del evento DNS abierto para un dominio/servidor, si existe."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM eventos_dns WHERE dominio = ? AND servidor_dns = ? AND fin IS NULL "
            "ORDER BY inicio DESC LIMIT 1",
            (dominio, servidor_dns),
        ).fetchone()
        if row:
            return row["id"]
    return None


# ---------------------------------------------------------------------------
# Helpers — mediciones de velocidad
# ---------------------------------------------------------------------------

def insert_medicion_velocidad(
    descarga_mbps: float,
    subida_mbps: float,
    ping_ms: float,
    latencia_bajo_carga_ms: Optional[float] = None,
) -> None:
    """Inserta una medición de velocidad, opcionalmente con latencia bajo carga."""
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO mediciones_velocidad "
            "(timestamp, descarga_mbps, subida_mbps, ping_ms, latencia_bajo_carga_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (now.isoformat(), descarga_mbps, subida_mbps, ping_ms, latencia_bajo_carga_ms),
        )
    logger.info(
        "Medición de velocidad: ↓%.2f Mbps ↑%.2f Mbps ping=%.1fms carga=%.1fms",
        descarga_mbps, subida_mbps, ping_ms,
        latencia_bajo_carga_ms if latencia_bajo_carga_ms is not None else 0.0,
    )


# ---------------------------------------------------------------------------
# Helpers — uso de ancho de banda
# ---------------------------------------------------------------------------

def insert_uso_banda(bytes_in: int, bytes_out: int) -> None:
    """Inserta un registro de uso de ancho de banda (delta, no acumulado)."""
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO uso_ancho_banda (timestamp, bytes_in, bytes_out) VALUES (?, ?, ?)",
            (now.isoformat(), bytes_in, bytes_out),
        )
    logger.info(
        "Uso de banda: in=%d bytes out=%d bytes", bytes_in, bytes_out,
    )


# ---------------------------------------------------------------------------
# Helpers — metadatos del sistema
# ---------------------------------------------------------------------------

def set_metadata(clave: str, valor: str) -> None:
    """Guarda o actualiza una clave/valor en la tabla metadata."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO metadata (clave, valor) VALUES (?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (clave, valor),
        )
    logger.info("Metadata actualizada: %s = %s", clave, valor)


def get_metadata(clave: str) -> Optional[str]:
    """Obtiene el valor de una clave desde metadata."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT valor FROM metadata WHERE clave = ?", (clave,)
        ).fetchone()
        if row:
            return row["valor"]
    return None


# ---------------------------------------------------------------------------
# Helpers — probe liviano y degradación de velocidad
# ---------------------------------------------------------------------------

import statistics

def insert_medicion_probe_liviano(
    mbps_aproximado: float,
    tiempo_respuesta_ms: float,
    servidor: Optional[str] = None,
    dns_ms: Optional[float] = None,
    tcp_connect_ms: Optional[float] = None,
    tls_ms: Optional[float] = None,
    ttfb_ms: Optional[float] = None,
    transfer_ms: Optional[float] = None,
    mbps_throughput: Optional[float] = None,
    latencia_ms: Optional[float] = None,
    streams_usados: int = 1,
    muestra_valida: bool = True,
) -> None:
    """Inserta una medición del probe liviano de velocidad con descomposición de tiempos."""
    now = datetime.now(timezone.utc)
    valida_int = 1 if muestra_valida else 0
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO mediciones_probe_liviano "
            "(timestamp, mbps_aproximado, tiempo_respuesta_ms, servidor, "
            "dns_ms, tcp_connect_ms, tls_ms, ttfb_ms, transfer_ms, mbps_throughput, "
            "latencia_ms, streams_usados, muestra_valida) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now.isoformat(),
                mbps_aproximado,
                tiempo_respuesta_ms,
                servidor,
                dns_ms,
                tcp_connect_ms,
                tls_ms,
                ttfb_ms,
                transfer_ms,
                mbps_throughput,
                latencia_ms,
                streams_usados,
                valida_int,
            ),
        )
    logger.info(
        "db: Probe liviano (nodo=%s, streams=%d, valida=%s): throughput=%.2f Mbps (aprox=%.2f Mbps) DNS=%.1fms TCP=%.1fms TLS=%.1fms TTFB=%.1fms Transfer=%.1fms",
        servidor or "N/A", streams_usados, "OK" if muestra_valida else "INVAL",
        mbps_throughput if mbps_throughput is not None else mbps_aproximado,
        mbps_aproximado,
        dns_ms or 0.0, tcp_connect_ms or 0.0, tls_ms or 0.0, ttfb_ms or 0.0, transfer_ms or 0.0
    )


def get_baseline_mbps_oficial(limit: int = 20, min_count: int = 20) -> Optional[float]:
    """Calcula el baseline de velocidad (promedio de descarga_mbps) usando estrictamente
    las últimas 'limit' mediciones del test oficial (Ookla en mediciones_velocidad).
    
    Retorna None si no hay suficientes datos (< min_count mediciones).
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT descarga_mbps FROM mediciones_velocidad "
            "WHERE descarga_mbps IS NOT NULL AND descarga_mbps > 0 "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()

        if len(rows) < min_count:
            return None

        total = sum(r["descarga_mbps"] for r in rows)
        return total / len(rows)


def get_baseline_mbps_liviano(limit: int = 20, min_count: int = 20) -> Optional[float]:
    """Calcula el baseline de velocidad usando la MEDIANA (statistics.median) de throughput_mbps
    sobre las últimas 'limit' mediciones sanas (is_valid = 1 / muestra_valida = 1).
    
    Busca primero en v2_l1_readings (V2) y hace fallback a mediciones_probe_liviano (V1).
    Retorna None si no hay suficientes datos sanos (< min_count mediciones).
    """
    with get_connection() as conn:
        # 1. Intentar V2 (v2_l1_readings)
        v2_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_l1_readings'"
        ).fetchone()
        if v2_check:
            rows_v2 = conn.execute(
                "SELECT throughput_mbps FROM v2_l1_readings "
                "WHERE (is_valid = 1 OR is_valid IS NULL) "
                "AND throughput_mbps > 0 "
                "ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            if len(rows_v2) >= min_count:
                vals = [r["throughput_mbps"] for r in rows_v2]
                return statistics.median(vals)

        # 2. Fallback a V1 (mediciones_probe_liviano)
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mediciones_probe_liviano'"
        ).fetchone()
        if not table_check:
            return None

        rows = conn.execute(
            "SELECT COALESCE(mbps_throughput, mbps_aproximado) as mbps FROM mediciones_probe_liviano "
            "WHERE (muestra_valida = 1 OR muestra_valida IS NULL) "
            "AND COALESCE(mbps_throughput, mbps_aproximado) > 0 "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()

        if len(rows) < min_count:
            return None

        vals = [r["mbps"] for r in rows]
        return statistics.median(vals)




def insert_evento_degradacion(
    severidad: str,
    fuente: str,
    baseline_mbps: float,
    velocidad_mbps: float,
    inicio: datetime,
) -> int:
    """Abre un nuevo evento de degradación de velocidad. Retorna el ID insertado."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO eventos_degradacion "
            "(severidad, fuente, baseline_mbps, velocidad_mbps, inicio) "
            "VALUES (?, ?, ?, ?, ?)",
            (severidad, fuente, baseline_mbps, velocidad_mbps, inicio.isoformat()),
        )
        event_id = cursor.lastrowid
        logger.info(
            "Evento de degradación abierto id=%d severidad=%s fuente=%s velocidad=%.2f Mbps (baseline=%.2f Mbps)",
            event_id, severidad, fuente, velocidad_mbps, baseline_mbps,
        )
        return event_id


def close_evento_degradacion(event_id: int, fin: datetime) -> None:
    """Cierra un evento de degradación existente, calculando la duración."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT inicio FROM eventos_degradacion WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            logger.warning("Evento degradación id=%d no encontrado al intentar cerrar", event_id)
            return

        inicio = datetime.fromisoformat(row["inicio"])
        duracion = int((fin - inicio).total_seconds())
        conn.execute(
            "UPDATE eventos_degradacion SET fin = ?, duracion_segundos = ? WHERE id = ?",
            (fin.isoformat(), duracion, event_id),
        )
        logger.info(
            "Evento de degradación cerrado id=%d fin=%s duración=%ds",
            event_id, fin.isoformat(), duracion,
        )


def get_open_evento_degradacion(fuente: str) -> Optional[dict]:
    """Retorna los datos del evento de degradación abierto para una fuente ('oficial' | 'liviano')."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, severidad, fuente, baseline_mbps, velocidad_mbps, inicio "
            "FROM eventos_degradacion WHERE fuente = ? AND fin IS NULL ORDER BY inicio DESC LIMIT 1",
            (fuente,),
        ).fetchone()
        if row:
            return dict(row)
    return None


# ---------------------------------------------------------------------------
# Helpers V2 — Sensores, FSM Eventos y Trazabilidad de Transiciones
# ---------------------------------------------------------------------------

import json


def insert_v2_l0_reading(
    timestamp: datetime,
    is_reachable: bool,
    target: str,
    latency_ms: Optional[float] = None,
    packet_loss_pct: Optional[float] = None,
    sub_checks: Optional[dict] = None,
) -> None:
    """Inserta una lectura del Sensor L0 (Conectividad/Alcance)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO v2_l0_readings "
            "(timestamp, is_reachable, target, latency_ms, packet_loss_pct, sub_checks) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                timestamp.isoformat(),
                1 if is_reachable else 0,
                target,
                latency_ms,
                packet_loss_pct,
                json.dumps(sub_checks) if sub_checks else None,
            ),
        )


def insert_v2_l1_reading(
    timestamp: datetime,
    throughput_mbps: float,
    total_time_ms: float,
    dns_ms: Optional[float] = None,
    tcp_ms: Optional[float] = None,
    tls_ms: Optional[float] = None,
    ttfb_ms: Optional[float] = None,
    transfer_ms: Optional[float] = None,
    streams_used: int = 1,
    baseline_mbps: Optional[float] = None,
    is_degraded: bool = False,
    is_valid: bool = True,
) -> None:
    """Inserta una lectura del Sensor L1 (Probe Liviano de Rendimiento)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO v2_l1_readings "
            "(timestamp, throughput_mbps, total_time_ms, dns_ms, tcp_ms, tls_ms, ttfb_ms, "
            "transfer_ms, streams_used, baseline_mbps, is_degraded, is_valid) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                timestamp.isoformat(),
                throughput_mbps,
                total_time_ms,
                dns_ms,
                tcp_ms,
                tls_ms,
                ttfb_ms,
                transfer_ms,
                streams_used,
                baseline_mbps,
                1 if is_degraded else 0,
                1 if is_valid else 0,
            ),
        )


def insert_v2_l2_reading(
    timestamp: datetime,
    download_mbps: float,
    upload_mbps: float,
    ping_ms: float,
    loaded_latency_ms: Optional[float] = None,
    baseline_mbps: Optional[float] = None,
    server_id: Optional[int] = None,
    server_name: Optional[str] = None,
) -> None:
    """Inserta una lectura del Sensor L2 (Ookla Speedtest de Alta Precisión)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO v2_l2_readings "
            "(timestamp, download_mbps, upload_mbps, ping_ms, loaded_latency_ms, "
            "baseline_mbps, server_id, server_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                timestamp.isoformat(),
                download_mbps,
                upload_mbps,
                ping_ms,
                loaded_latency_ms,
                baseline_mbps,
                server_id,
                server_name,
            ),
        )


def insert_v2_fsm_transition(
    timestamp: datetime,
    current_state: str,
    input_symbol: str,
    next_state: str,
    output_action: str,
    is_reentry: bool = False,
    readings_json: Optional[dict] = None,
) -> None:
    """Registra un paso o transición en la máquina de estados FSM Mealy."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO v2_fsm_history "
            "(timestamp, current_state, input_symbol, next_state, output_action, is_reentry, readings_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                timestamp.isoformat(),
                current_state,
                input_symbol,
                next_state,
                output_action,
                1 if is_reentry else 0,
                json.dumps(readings_json) if readings_json else None,
            ),
        )


def insert_v2_event(
    event_type: str,
    state_origin: str,
    start_time: datetime,
    l0_status: Optional[str] = None,
    l1_throughput: Optional[float] = None,
    l2_download: Optional[float] = None,
    diagnosis_code: Optional[str] = None,
    diagnosis_detail: Optional[dict] = None,
    evidence: Optional[dict] = None,
) -> int:
    """Abre un nuevo evento en V2. Retorna el ID generado."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO v2_events "
            "(event_type, state_origin, start_time, l0_status, l1_throughput, l2_download, "
            "diagnosis_code, diagnosis_detail, evidence_json, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (
                event_type,
                state_origin,
                start_time.isoformat(),
                l0_status,
                l1_throughput,
                l2_download,
                diagnosis_code,
                json.dumps(diagnosis_detail) if diagnosis_detail else None,
                json.dumps(evidence) if evidence else None,
            ),
        )
        event_id = cursor.lastrowid
        logger.info(
            "Evento V2 abierto id=%d tipo=%s origen=%s inicio=%s",
            event_id, event_type, state_origin, start_time.isoformat(),
        )
        return event_id


def close_v2_event(
    event_id: int,
    fin: datetime,
    diagnosis_code: Optional[str] = None,
    diagnosis_detail: Optional[dict] = None,
    evidence: Optional[dict] = None,
) -> None:
    """Cierra un evento V2 activo."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT start_time FROM v2_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not row:
            logger.warning("Evento V2 id=%d no encontrado al intentar cerrar", event_id)
            return

        inicio = datetime.fromisoformat(row["start_time"])
        duracion = int((fin - inicio).total_seconds())
        
        updates = ["end_time = ?", "duration_seconds = ?", "is_active = 0"]
        params = [fin.isoformat(), duracion]

        if diagnosis_code:
            updates.append("diagnosis_code = ?")
            params.append(diagnosis_code)
        if diagnosis_detail:
            updates.append("diagnosis_detail = ?")
            params.append(json.dumps(diagnosis_detail))
        if evidence:
            updates.append("evidence_json = ?")
            params.append(json.dumps(evidence))

        params.append(event_id)
        sql = f"UPDATE v2_events SET {', '.join(updates)} WHERE id = ?"
        conn.execute(sql, params)
        logger.info(
            "Evento V2 cerrado id=%d fin=%s duración=%ds",
            event_id, fin.isoformat(), duracion,
        )


def get_active_v2_event() -> Optional[dict]:
    """Retorna el evento V2 actualmente activo (is_active = 1), si existe."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM v2_events WHERE is_active = 1 ORDER BY start_time DESC LIMIT 1"
        ).fetchone()
        if row:
            return dict(row)
    return None


def get_v2_fsm_history(limit: int = 50) -> list[dict]:
    """Retorna los últimos N registros del historial de transiciones FSM."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM v2_fsm_history ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_v2_events(limit: int = 50) -> list[dict]:
    """Retorna los últimos N eventos V2."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM v2_events ORDER BY start_time DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

