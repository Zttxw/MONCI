"""Router de eventos DNS — GET /dns?desde=&hasta=."""

from fastapi import APIRouter, Query

from db import get_connection
from models import EventoDNS
from utils import parse_local_date_range_to_utc

router = APIRouter()


@router.get("/dns", response_model=list[EventoDNS])
async def get_eventos_dns(
    desde: str = Query("2020-01-01", description="Fecha inicio (YYYY-MM-DD)"),
    hasta: str = Query("2099-12-31", description="Fecha fin (YYYY-MM-DD)"),
):
    """Historial de eventos de falla DNS filtrado por rango de fechas."""
    start_utc, end_utc = parse_local_date_range_to_utc(desde, hasta)

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM eventos_dns "
            "WHERE inicio >= ? AND inicio <= ? "
            "ORDER BY inicio DESC",
            (start_utc, end_utc),
        ).fetchall()

    return [EventoDNS(**dict(row)) for row in rows]
