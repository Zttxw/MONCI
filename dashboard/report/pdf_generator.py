"""Generador de reportes PDF con ReportLab.

Genera un reporte exportable para presentar al ISP con:
- Encabezado con rango de fechas
- Tabla resumen: total caídas, tiempo sin servicio, disponibilidad %
- Tabla detallada de eventos de caída
- Tabla de mediciones de velocidad
- Pie de página con fecha de generación
"""

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)


def generate_report_pdf(
    caidas: list[dict],
    velocidades: list[dict],
    desde: str,
    hasta: str,
    eventos_dns: list[dict] | None = None,
    eventos_degradacion: list[dict] | None = None,
) -> bytes:

    """Genera un reporte PDF en memoria y retorna los bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=1.5 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Title'],
        fontSize=18,
        spaceAfter=6,
        textColor=colors.HexColor('#1e293b'),
    )

    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontSize=11,
        textColor=colors.HexColor('#64748b'),
        spaceAfter=20,
    )

    section_style = ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontSize=13,
        spaceBefore=16,
        spaceAfter=8,
        textColor=colors.HexColor('#334155'),
    )

    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.HexColor('#94a3b8'),
        alignment=1,  # CENTER
    )

    story = []

    # --- Header ---
    story.append(Paragraph("Reporte de Conectividad a Internet", title_style))
    story.append(Paragraph(
        f"Período: {desde} — {hasta}",
        subtitle_style,
    ))
    story.append(HRFlowable(
        width="100%", thickness=1,
        color=colors.HexColor('#e2e8f0'), spaceAfter=16,
    ))

    # --- Resumen ---
    story.append(Paragraph("Resumen", section_style))

    # Excluir eventos cerrados por cambio_destino de las métricas
    caidas_reales = [c for c in caidas if c.get("cierre_tipo") != "cambio_destino"]
    caidas_cambio = [c for c in caidas if c.get("cierre_tipo") == "cambio_destino"]

    total_caidas = len(caidas_reales)
    caidas_isp_general = sum(1 for c in caidas_reales if c.get("origen") == "isp_general")
    caidas_isp_primer = sum(1 for c in caidas_reales if c.get("origen") == "isp_primer_salto")
    caidas_isp_legacy = sum(1 for c in caidas_reales if c.get("origen") == "isp")
    caidas_local = sum(1 for c in caidas_reales if c.get("origen") == "red_local")

    # Calcular tiempo total sin servicio (solo caídas reales).
    now_utc = datetime.now(timezone.utc)
    tiempo_total = 0
    for c in caidas_reales:
        if c.get("duracion_segundos") is not None:
            tiempo_total += c["duracion_segundos"]
        elif c.get("inicio"):
            try:
                inicio_dt = datetime.fromisoformat(c["inicio"])
                tiempo_total += int((now_utc - inicio_dt).total_seconds())
            except Exception:
                pass

    # Calcular disponibilidad
    try:
        desde_dt = datetime.fromisoformat(desde)
        hasta_dt = datetime.fromisoformat(hasta + "T23:59:59")
        periodo_total = (hasta_dt - desde_dt).total_seconds()
        disponibilidad = ((periodo_total - tiempo_total) / periodo_total * 100) if periodo_total > 0 else 100
    except Exception:
        disponibilidad = 0

    resumen_data = [
        ["Métrica", "Valor"],
        ["Total de caídas (excl. cambios de destino)", str(total_caidas)],
        ["Caídas — ISP general", str(caidas_isp_general + caidas_isp_legacy)],
        ["Caídas — ISP primer salto", str(caidas_isp_primer)],
        ["Caídas — Red local", str(caidas_local)],
        ["Cierres por cambio de destino", str(len(caidas_cambio))],
        ["Tiempo total sin servicio", _format_duration(tiempo_total)],
        ["Disponibilidad en el período", f"{disponibilidad:.2f}%"],
    ]

    # Agregar resumen de DNS si hay datos
    if eventos_dns:
        dns_total = len(eventos_dns)
        dns_tiempo = 0
        for d in eventos_dns:
            if d.get("duracion_segundos") is not None:
                dns_tiempo += d["duracion_segundos"]
            elif d.get("inicio"):
                try:
                    inicio_dt = datetime.fromisoformat(d["inicio"])
                    dns_tiempo += int((now_utc - inicio_dt).total_seconds())
                except Exception:
                    pass
        resumen_data.append(["Fallas DNS registradas", str(dns_total)])
        resumen_data.append(["Tiempo total sin DNS", _format_duration(dns_tiempo)])

    t = Table(resumen_data, colWidths=[3.5 * inch, 3 * inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#334155')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))

    # --- Detalle de caídas ---
    if caidas:
        story.append(Paragraph("Detalle de eventos de caída", section_style))

        caida_header = ["#", "Destino", "Origen", "Inicio", "Fin", "Duración", "Cierre"]
        caida_rows = [caida_header]
        for i, c in enumerate(caidas, 1):
            if c.get("duracion_segundos") is not None:
                dur_str = _format_duration(c["duracion_segundos"])
            elif c.get("inicio"):
                try:
                    inicio_dt = datetime.fromisoformat(c["inicio"])
                    parcial = int((now_utc - inicio_dt).total_seconds())
                    dur_str = f"{_format_duration(parcial)} (en curso)"
                except Exception:
                    dur_str = "En curso"
            else:
                dur_str = "—"

            cierre = c.get("cierre_tipo", "")
            if cierre == "cambio_destino":
                cierre_str = "Cambio dest."
            elif cierre == "recuperacion":
                cierre_str = "Recuperación"
            else:
                cierre_str = "—" if c.get("fin") else ""

            caida_rows.append([
                str(i),
                c.get("destino", ""),
                c.get("origen", "").upper(),
                _format_datetime(c.get("inicio")),
                _format_datetime(c.get("fin")) if c.get("fin") else "En curso",
                dur_str,
                cierre_str,
            ])

        col_widths = [0.35 * inch, 0.9 * inch, 1.0 * inch, 1.3 * inch, 1.3 * inch, 0.7 * inch, 0.8 * inch]
        t2 = Table(caida_rows, colWidths=col_widths, repeatRows=1)
        t2.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#334155')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')]),
        ]))
        story.append(t2)
        story.append(Spacer(1, 16))

    # --- Eventos DNS ---
    if eventos_dns:
        story.append(Paragraph("Eventos de falla DNS", section_style))

        dns_header = ["#", "Dominio", "Servidor DNS", "Inicio", "Fin", "Duración"]
        dns_rows = [dns_header]
        for i, d in enumerate(eventos_dns, 1):
            if d.get("duracion_segundos") is not None:
                dur_str = _format_duration(d["duracion_segundos"])
            elif d.get("inicio"):
                try:
                    inicio_dt = datetime.fromisoformat(d["inicio"])
                    parcial = int((now_utc - inicio_dt).total_seconds())
                    dur_str = f"{_format_duration(parcial)} (en curso)"
                except Exception:
                    dur_str = "En curso"
            else:
                dur_str = "—"

            dns_rows.append([
                str(i),
                d.get("dominio", ""),
                d.get("servidor_dns", ""),
                _format_datetime(d.get("inicio")),
                _format_datetime(d.get("fin")) if d.get("fin") else "En curso",
                dur_str,
            ])

        col_widths = [0.4 * inch, 1.2 * inch, 1.0 * inch, 1.5 * inch, 1.5 * inch, 0.9 * inch]
        t_dns = Table(dns_rows, colWidths=col_widths, repeatRows=1)
        t_dns.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#334155')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')]),
        ]))
        story.append(t_dns)
        story.append(Spacer(1, 16))

    # --- Eventos de degradación de velocidad ---
    # Filtrar micro-variaciones transitorias (< 120s) del probe liviano
    eventos_deg_reales = [
        d for d in (eventos_degradacion or [])
        if d.get("fuente") == "oficial" or d.get("duracion_segundos") is None or d.get("duracion_segundos") >= 120
    ]

    if eventos_deg_reales:
        story.append(Paragraph("Eventos de degradación de velocidad", section_style))

        deg_header = [
            "#", "Fuente", "Severidad", "Medido", "Baseline", "Inicio", "Fin", "Duración"
        ]
        deg_rows = [deg_header]
        for i, d in enumerate(eventos_deg_reales, 1):
            fuente_str = "Oficial (Ookla)" if d.get("fuente") == "oficial" else "Liviano (Cloudflare)"
            sev_str = str(d.get("severidad", "")).capitalize()

            if d.get("duracion_segundos") is not None:
                dur_str = _format_duration(d["duracion_segundos"])
            elif d.get("inicio"):
                try:
                    inicio_dt = datetime.fromisoformat(d["inicio"])
                    parcial = int((now_utc - inicio_dt).total_seconds())
                    dur_str = f"{_format_duration(parcial)} (en curso)"
                except Exception:
                    dur_str = "En curso"
            else:
                dur_str = "—"

            deg_rows.append([
                str(i),
                fuente_str,
                sev_str,
                f"{d.get('velocidad_mbps', 0):.1f} Mbps",
                f"{d.get('baseline_mbps', 0):.1f} Mbps",
                _format_datetime(d.get("inicio")),
                _format_datetime(d.get("fin")) if d.get("fin") else "En curso",
                dur_str,
            ])

        col_widths = [0.3 * inch, 1.2 * inch, 0.7 * inch, 0.9 * inch, 0.9 * inch, 1.2 * inch, 1.2 * inch, 0.8 * inch]
        t_deg = Table(deg_rows, colWidths=col_widths, repeatRows=1)
        t_deg.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#334155')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7.5),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')]),
        ]))
        story.append(t_deg)
        story.append(Spacer(1, 16))

    # --- Mediciones de velocidad ---
    if velocidades:

        story.append(Paragraph("Mediciones de velocidad", section_style))

        vel_header = ["#", "Fecha/Hora", "Descarga (Mbps)", "Subida (Mbps)", "Ping (ms)", "Carga (ms)"]
        vel_rows = [vel_header]
        for i, v in enumerate(velocidades, 1):
            carga_str = f"{v.get('latencia_bajo_carga_ms', 0):.0f}" if v.get("latencia_bajo_carga_ms") else "—"
            vel_rows.append([
                str(i),
                _format_datetime(v.get("timestamp")),
                f"{v.get('descarga_mbps', 0):.1f}" if v.get("descarga_mbps") else "—",
                f"{v.get('subida_mbps', 0):.1f}" if v.get("subida_mbps") else "—",
                f"{v.get('ping_ms', 0):.0f}" if v.get("ping_ms") else "—",
                carga_str,
            ])

        col_widths = [0.35 * inch, 1.5 * inch, 1.1 * inch, 1.1 * inch, 0.8 * inch, 0.8 * inch]
        t3 = Table(vel_rows, colWidths=col_widths, repeatRows=1)
        t3.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#334155')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ALIGN', (2, 1), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')]),
        ]))
        story.append(t3)

    # --- Footer ---
    story.append(Spacer(1, 30))
    story.append(HRFlowable(
        width="100%", thickness=0.5,
        color=colors.HexColor('#e2e8f0'), spaceAfter=8,
    ))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    story.append(Paragraph(
        f"Reporte generado automáticamente el {now} — Control Internet v2.0",
        footer_style,
    ))

    doc.build(story)
    return buffer.getvalue()


def _format_duration(seconds: int) -> str:
    """Formatea una duración en segundos a formato legible."""
    if not seconds:
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m = seconds // 60
        s = seconds % 60
        return f"{m}m {s}s"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m}m"


def _format_datetime(iso_str: str | None) -> str:
    """Formatea un ISO datetime a formato legible."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str
