"""Router de velocidad — GET /velocidad?desde=&hasta=."""

from fastapi import APIRouter, Query

from db import get_connection
from models import EventoDegradacion, MedicionProbeLiviano, MedicionVelocidad
from utils import parse_local_date_range_to_utc

router = APIRouter()


@router.get("/velocidad", response_model=list[MedicionVelocidad])
async def get_velocidad(
    desde: str = Query("2020-01-01", description="Fecha inicio (YYYY-MM-DD)"),
    hasta: str = Query("2099-12-31", description="Fecha fin (YYYY-MM-DD)"),
):
    """Historial de mediciones de velocidad oficial (Ookla) filtrado por rango de fechas."""
    start_utc, end_utc = parse_local_date_range_to_utc(desde, hasta)

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM mediciones_velocidad "
            "WHERE timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp ASC",
            (start_utc, end_utc),
        ).fetchall()

    return [MedicionVelocidad(**dict(row)) for row in rows]


@router.get("/probe-liviano", response_model=list[MedicionProbeLiviano])
async def get_probe_liviano(
    desde: str = Query("2020-01-01", description="Fecha inicio (YYYY-MM-DD)"),
    hasta: str = Query("2099-12-31", description="Fecha fin (YYYY-MM-DD)"),
):
    """Historial de mediciones del probe liviano de velocidad (Cloudflare)."""
    start_utc, end_utc = parse_local_date_range_to_utc(desde, hasta)

    with get_connection() as conn:
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mediciones_probe_liviano'"
        ).fetchone()
        if not table_check:
            return []

        rows = conn.execute(
            "SELECT * FROM mediciones_probe_liviano "
            "WHERE timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp ASC",
            (start_utc, end_utc),
        ).fetchall()

    return [MedicionProbeLiviano(**dict(row)) for row in rows]


@router.get("/degradaciones", response_model=list[EventoDegradacion])
async def get_degradaciones(
    desde: str = Query("2020-01-01", description="Fecha inicio (YYYY-MM-DD)"),
    hasta: str = Query("2099-12-31", description="Fecha fin (YYYY-MM-DD)"),
):
    """Historial de eventos de degradación de velocidad."""
    start_utc, end_utc = parse_local_date_range_to_utc(desde, hasta)

    with get_connection() as conn:
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='eventos_degradacion'"
        ).fetchone()
        if not table_check:
            return []

        rows = conn.execute(
            "SELECT * FROM eventos_degradacion "
            "WHERE inicio >= ? AND inicio <= ? "
            "ORDER BY inicio ASC",
            (start_utc, end_utc),
        ).fetchall()

    return [EventoDegradacion(**dict(row)) for row in rows]


