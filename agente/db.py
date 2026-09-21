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
    """Calcula el baseline de velocidad usando la MEDIANA (statistics.median) de mbps_throughput
    (o mbps_aproximado para registros heredados) sobre las últimas 'limit' mediciones sanas (muestra_valida = 1).
    
    Retorna None si no hay suficientes datos sanos (< min_count mediciones).
    """
    with get_connection() as conn:
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mediciones_probe_liviano'"
        ).fetchone()
        if not table_check:
            return None

        # Filtrar únicamente muestras válidas (muestra_valida != 0)
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

