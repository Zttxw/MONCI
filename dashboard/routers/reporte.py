"""Router de reportes — GET /reporte/pdf?desde=&hasta=."""

import asyncio

from fastapi import APIRouter, Query
from fastapi.responses import Response

from db import get_connection
from report.pdf_generator import generate_report_pdf
from utils import parse_local_date_range_to_utc

router = APIRouter()


def _fetch_report_data(desde: str, hasta: str) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Obtiene datos de caídas, velocidad, DNS y degradaciones para el reporte (síncrono)."""
    start_utc, end_utc = parse_local_date_range_to_utc(desde, hasta)

    with get_connection() as conn:
        caidas_rows = conn.execute(
            "SELECT * FROM eventos_caida "
            "WHERE inicio >= ? AND inicio <= ? "
            "ORDER BY inicio ASC",
            (start_utc, end_utc),
        ).fetchall()

        vel_rows = conn.execute(
            "SELECT * FROM mediciones_velocidad "
            "WHERE timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp ASC",
            (start_utc, end_utc),
        ).fetchall()

        # Eventos DNS — tolerante a tablas que aún no existen
        try:
            dns_rows = conn.execute(
                "SELECT * FROM eventos_dns "
                "WHERE inicio >= ? AND inicio <= ? "
                "ORDER BY inicio ASC",
                (start_utc, end_utc),
            ).fetchall()
        except Exception:
            dns_rows = []

        # Eventos de degradación
        try:
            deg_rows = conn.execute(
                "SELECT * FROM eventos_degradacion "
                "WHERE inicio >= ? AND inicio <= ? "
                "ORDER BY inicio ASC",
                (start_utc, end_utc),
            ).fetchall()
        except Exception:
            deg_rows = []

    caidas = [dict(row) for row in caidas_rows]
    velocidades = [dict(row) for row in vel_rows]
    eventos_dns = [dict(row) for row in dns_rows]
    eventos_degradacion = [dict(row) for row in deg_rows]
    return caidas, velocidades, eventos_dns, eventos_degradacion


@router.get("/reporte/pdf")
async def get_reporte_pdf(
    desde: str = Query("2020-01-01", description="Fecha inicio (YYYY-MM-DD)"),
    hasta: str = Query("2099-12-31", description="Fecha fin (YYYY-MM-DD)"),
):
    """Genera y descarga un reporte PDF para presentar al ISP."""
    # Ejecutar en thread para no bloquear el event loop
    caidas, velocidades, eventos_dns, eventos_degradacion = await asyncio.to_thread(
        _fetch_report_data, desde, hasta
    )
    pdf_bytes = await asyncio.to_thread(
        generate_report_pdf, caidas, velocidades, desde, hasta, eventos_dns, eventos_degradacion
    )

    filename = f"reporte_internet_{desde}_{hasta}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

