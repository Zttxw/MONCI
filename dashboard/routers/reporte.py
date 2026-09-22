"""Router de reportes V2 — PDF auditable para ISP, firma .sha256 y preview JSON."""

import asyncio
from fastapi import APIRouter, Query, Response

from report.v2_report_data import fetch_v2_report_dataset
from report.v2_pdf_engine import build_v2_pdf_report
from report.pdf_generator import generate_report_pdf

router = APIRouter()


@router.get("/reporte/pdf")
@router.get("/api/v2/reporte/pdf")
async def get_reporte_pdf(
    desde: str = Query("2020-01-01 00:00:00", description="Fecha/Hora inicio (YYYY-MM-DD HH:MM:SS)"),
    hasta: str = Query("2099-12-31 23:59:59", description="Fecha/Hora fin (YYYY-MM-DD HH:MM:SS)"),
    tz: str = Query("America/Lima", description="Zona Horaria"),
):
    """Genera y descarga el Reporte Técnico PDF V2 auditable ante el ISP."""
    dataset = await asyncio.to_thread(fetch_v2_report_dataset, desde, hasta, tz)
    pdf_bytes, sha256_hex, filename_base = await asyncio.to_thread(build_v2_pdf_report, dataset)

    filename = f"{filename_base}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Report-SHA256": sha256_hex,
        },
    )


@router.get("/api/v2/reporte/sha256")
async def get_reporte_sha256(
    desde: str = Query("2020-01-01 00:00:00", description="Fecha/Hora inicio (YYYY-MM-DD HH:MM:SS)"),
    hasta: str = Query("2099-12-31 23:59:59", description="Fecha/Hora fin (YYYY-MM-DD HH:MM:SS)"),
    tz: str = Query("America/Lima", description="Zona Horaria"),
):
    """Genera y descarga el archivo .sha256 correspondiente al PDF del reporte."""
    dataset = await asyncio.to_thread(fetch_v2_report_dataset, desde, hasta, tz)
    pdf_bytes, sha256_hex, filename_base = await asyncio.to_thread(build_v2_pdf_report, dataset)

    content = f"{sha256_hex}  {filename_base}.pdf\n"
    filename = f"{filename_base}.pdf.sha256"

    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/v2/reporte/preview")
async def get_reporte_preview(
    desde: str = Query("2020-01-01 00:00:00", description="Fecha/Hora inicio (YYYY-MM-DD HH:MM:SS)"),
    hasta: str = Query("2099-12-31 23:59:59", description="Fecha/Hora fin (YYYY-MM-DD HH:MM:SS)"),
    tz: str = Query("America/Lima", description="Zona Horaria"),
):
    """Obtiene una previsualización JSON de los datos del reporte sin generar el PDF completo."""
    dataset = await asyncio.to_thread(fetch_v2_report_dataset, desde, hasta, tz)
    return dataset


