"""Router de caídas — GET /caidas?desde=&hasta=."""

from fastapi import APIRouter, Query

from db import get_connection
from models import EventoCaida
from utils import parse_local_date_range_to_utc

router = APIRouter()


@router.get("/caidas", response_model=list[EventoCaida])
async def get_caidas(
    desde: str = Query("2020-01-01", description="Fecha inicio (YYYY-MM-DD)"),
    hasta: str = Query("2099-12-31", description="Fecha fin (YYYY-MM-DD)"),
):
    """Historial de eventos de caída filtrado por rango de fechas."""
    start_utc, end_utc = parse_local_date_range_to_utc(desde, hasta)

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM eventos_caida "
            "WHERE inicio >= ? AND inicio <= ? "
            "ORDER BY inicio DESC",
            (start_utc, end_utc),
        ).fetchall()

    return [EventoCaida(**dict(row)) for row in rows]

