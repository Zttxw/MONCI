"""Acceso read-only a SQLite para el dashboard.

Reglas de diseño (ARCHITECTURE.md):
- Conexión vía URI file:...?mode=ro con uri=True (no :ro en volumen Docker)
- Conexión nueva por request (lecturas rápidas, sin check_same_thread)
- Retry con backoff en startup para esperar a que el agente cree la DB
"""

import sqlite3
import time
import logging
from contextlib import contextmanager

from config import DB_URI

logger = logging.getLogger(__name__)


def wait_for_db(max_wait: int = 30, interval: int = 2) -> None:
    """Espera a que la base de datos esté disponible (retry con backoff).

    Defensa en profundidad contra riesgo #3: depends_on no garantiza
    que la DB exista cuando el dashboard arranca.
    """
    elapsed = 0
    while elapsed < max_wait:
        try:
            conn = sqlite3.connect(DB_URI, uri=True)
            conn.execute("SELECT 1 FROM eventos_caida LIMIT 0")
            conn.close()
            logger.info("Base de datos disponible en %s", DB_URI)
            return
        except Exception as e:
            logger.warning(
                "DB no disponible (%s), reintentando en %ds... (%d/%ds)",
                e, interval, elapsed, max_wait,
            )
            time.sleep(interval)
            elapsed += interval

    raise RuntimeError(
        f"No se pudo conectar a la base de datos después de {max_wait}s"
    )


@contextmanager
def get_connection():
    """Context manager — abre una conexión read-only por request.

    Usa URI con ?mode=ro para impedir escritura desde el dashboard,
    y uri=True para que Python interprete la URI correctamente.
    """
    conn = sqlite3.connect(DB_URI, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_metadata(clave: str) -> str | None:
    """Obtiene un valor desde la tabla metadata de forma read-only."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT valor FROM metadata WHERE clave = ?", (clave,)
            ).fetchone()
            if row:
                return row["valor"]
    except Exception:
        pass
    return None

