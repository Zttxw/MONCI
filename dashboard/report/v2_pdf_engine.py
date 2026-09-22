"""Motor de Generación PDF V2 para Reportes Técnicos Auditales con ReportLab.

Diseñado con rigor probatorio para presentar ante Proveedores de Internet (ISP).
Sigue un diseño claro y limpio optimizado para impresión (Light Mode).
"""

import hashlib
import io
from datetime import datetime, timezone
from typing import Tuple, List, Dict, Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak, KeepTogether
)
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Group, Polygon
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.barcharts import VerticalBarChart


class NumberedCanvas(canvas.Canvas):
    """Canvas de dos pasadas para calcular dinámicamente 'Página X de Y'."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count: int):
        # La portada (Página 1) no lleva encabezado/pie estándar
        if self._pageNumber == 1:
            return

        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))

        # Encabezado (Header)
        header_text = "TRACEFLY V2 — REPORTE TÉCNICO DE MONITOREO DE CONECTIVIDAD"
        self.drawString(2 * cm, 26.5 * cm, header_text)
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(2 * cm, 26.3 * cm, 19.5 * cm, 26.3 * cm)

        # Pie de página (Footer)
        page_str = f"Página {self._pageNumber} de {page_count}"
        footer_text = f"Reporte técnico de evidencia | {page_str}"
        self.drawString(2 * cm, 1.2 * cm, footer_text)
        self.drawRightString(19.5 * cm, 1.2 * cm, "ControlInternet V2 / Tracefly V2")
        self.line(2 * cm, 1.5 * cm, 19.5 * cm, 1.5 * cm)

        self.restoreState()


def build_v2_pdf_report(data: Dict[str, Any]) -> Tuple[bytes, str, str]:
    """Genera el PDF del Reporte Técnico V2 y calcula su firma SHA-256.

    Retorna: (pdf_bytes, sha256_hex, filename_base)
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=2.2 * cm,
        bottomMargin=2.2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
    )

    styles = getSampleStyleSheet()

    # Custom Printable Light Styles
    cover_title_style = ParagraphStyle(
        'CoverTitle',
        parent=styles['Title'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0F172A'),
        alignment=0,  # Left
        spaceAfter=12,
    )

    cover_subtitle_style = ParagraphStyle(
        'CoverSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor('#65A30D'),  # Volt Accent Printable
        spaceAfter=24,
    )

    h1_style = ParagraphStyle(
        'Heading1_V2',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        textColor=colors.HexColor('#0F172A'),
        spaceBefore=16,
        spaceAfter=8,
        keepWithNext=True,
    )

    h2_style = ParagraphStyle(
        'Heading2_V2',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=13,
        textColor=colors.HexColor('#334155'),
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        'Body_V2',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor('#334155'),
        spaceAfter=6,
    )

    body_bold = ParagraphStyle(
        'BodyBold_V2',
        parent=body_style,
        fontName='Helvetica-Bold',
    )

    alert_style = ParagraphStyle(
        'Alert_V2',
        parent=body_style,
        fontName='Helvetica-Oblique',
        fontSize=8,
        leading=11,
        textColor=colors.HexColor('#B45309'),  # Amber dark
        spaceBefore=6,
        spaceAfter=6,
    )

    code_style = ParagraphStyle(
        'Code_V2',
        parent=styles['Code'],
        fontName='Courier',
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor('#1E293B'),
    )

    story = []

    # =========================================================================
    # 1. PORTADA Y HUELLA DE INTEGRIDAD (Página 1)
    # =========================================================================
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph("TRACEFLY V2", ParagraphStyle('SubBrand', fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor('#65A30D'), spaceAfter=4)))
    story.append(Paragraph("REPORTE TÉCNICO DE MONITOREO DE CONECTIVIDAD Y RENDIMIENTO", cover_title_style))
    story.append(Paragraph("EVIDENCIA AUDITABLE PARA PRESENTAR ANTE EL PROVEEDOR DE INTERNET (ISP)", cover_subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#0F172A'), spaceAfter=20))

    # Tabla de Identidad del Reporte
    ident_data = [
        [Paragraph("<b>Identificador del Reporte:</b>", body_style), Paragraph(data.get("report_id", "—"), body_bold)],
        [Paragraph("<b>Sistema:</b>", body_style), Paragraph("ControlInternet V2 (Tracefly Architecture)", body_style)],
        [Paragraph("<b>Versión de Arquitectura:</b>", body_style), Paragraph("Mealy FSM V2.0 (L0 / L1 / Fast.com / Ookla)", body_style)],
        [Paragraph("<b>Identificador del Agente:</b>", body_style), Paragraph(str(data.get("agent_id", "No especificado / Agente Local")), body_style)],
        [Paragraph("<b>Período Analizado:</b>", body_style), Paragraph(f"Desde: {data.get('desde')}<br/>Hasta: {data.get('hasta')}", body_style)],
        [Paragraph("<b>Fecha/Hora de Generación:</b>", body_style), Paragraph(data.get("generation_time", "—"), body_style)],
        [Paragraph("<b>Zona Horaria:</b>", body_style), Paragraph(data.get("tz", "America/Lima"), body_style)],
        [Paragraph("<b>Precisión Temporal:</b>", body_style), Paragraph("Cronología temporal basada en los timestamps registrados por el sistema.", body_style)],
    ]

    t_ident = Table(ident_data, colWidths=[2.2 * inch, 4.5 * inch])
    t_ident.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(t_ident)
    story.append(Spacer(1, 20))

    # Bloque de Integridad SHA-256
    story.append(Paragraph("<b>ESTADO DE INTEGRIDAD DEL REPORTE</b>", h2_style))
    sha_box = [
        [Paragraph("<b>Huella digital SHA-256:</b>", body_bold)],
        [Paragraph("Disponible en el archivo de integridad asociado (<code>.sha256</code>).", body_style)],
        [Paragraph("<i>Este documento PDF y su firma `.sha256` garantizan la auditabilidad inalterable de las observaciones almacenadas en la base de datos SQLite.</i>", alert_style)],
    ]
    t_sha = Table(sha_box, colWidths=[6.7 * inch])
    t_sha.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F1F5F9')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#CBD5E1')),
        ('PADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(t_sha)
    story.append(PageBreak())

    # =========================================================================
    # 2. RESUMEN EJECUTIVO (Página 2)
    # =========================================================================
    story.append(Paragraph("1. RESUMEN EJECUTIVO", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#E2E8F0'), spaceAfter=12))

    if data.get("version_warning"):
        warn_table = Table([[Paragraph(f"<b>ADVERTENCIA DE VERSIÓN:</b><br/>{data['version_warning']}", alert_style)]], colWidths=[6.7 * inch])
        warn_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FEF3C7')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#F59E0B')),
            ('PADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(warn_table)
        story.append(Spacer(1, 10))

    kpis = data.get("kpis", {})
    resumen_table_data = [
        [Paragraph("<b>Métrica de Monitoreo</b>", body_bold), Paragraph("<b>Resultado Observado</b>", body_bold)],
        [Paragraph("Tiempo Total Monitoreado", body_style), Paragraph(data.get("periodo_monitoreado_str", "—"), body_style)],
        [Paragraph("Total de Eventos Detectados", body_style), Paragraph(str(kpis.get("total_eventos", 0)), body_style)],
        [Paragraph("Degradaciones Confirmadas (<code>cd</code>)", body_style), Paragraph(str(kpis.get("degradaciones", 0)), body_style)],
        [Paragraph("Caídas Totales Confirmadas (<code>co</code>)", body_style), Paragraph(str(kpis.get("caidas", 0)), body_style)],
        [Paragraph("Eventos Recuperados", body_style), Paragraph(str(kpis.get("recuperados", 0)), body_style)],
        [Paragraph("Eventos Activos al Cierre", body_style), Paragraph(str(kpis.get("activos", 0)), body_style)],
        [Paragraph("<b>Disponibilidad de Conectividad (L0)</b>", body_bold), Paragraph(f"<b>{kpis.get('disponibilidad_pct', '100.00%')}</b>", body_bold)],
        [Paragraph("Tiempo Total en Degradación", body_style), Paragraph(kpis.get("tiempo_degradado_str", "0 s"), body_style)],
        [Paragraph("Tiempo Total en Caída Total", body_style), Paragraph(kpis.get("tiempo_caida_str", "0 s"), body_style)],
        [Paragraph("<b>Tiempo Afectado Total (Degradación + Caída)</b>", body_bold), Paragraph(f"<b>{kpis.get('tiempo_afectado_str', '0 s')}</b>", body_bold)],
    ]

    t_resumen = Table(resumen_table_data, colWidths=[4.2 * inch, 2.5 * inch])
    t_resumen.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
    ]))
    story.append(t_resumen)
    story.append(Spacer(1, 16))

    # =========================================================================
    # 3. REGISTRO DE INCIDENTES Y EVIDENCIA DETALLADA (PRIORIDAD NÚCLEO)
    # =========================================================================
    story.append(Paragraph("2. REGISTRO DE INCIDENTES Y EVIDENCIA DETALLADA", h1_style))
    story.append(Paragraph("Esta sección constituye el núcleo probatorio del reporte técnico. Cada incidente incluye su evolución temporal, los sensores que participaron y el registro de mediciones almacenadas.", body_style))
    story.append(Spacer(1, 10))

    incidentes = data.get("incidentes", [])
    if not incidentes:
        story.append(Paragraph("<i>No se registraron incidentes de degradación o caída durante el período seleccionado. La conectividad se mantuvo en estado NORMAL.</i>", body_style))
        story.append(Spacer(1, 16))
    else:
        for inc in incidentes:
            inc_flow = []
            inc_flow.append(Paragraph(f"<b>{inc['id']} — {inc['init_type'].upper()}</b>", h2_style))
            
            # Metadata de evolución
            meta_inc = [
                [Paragraph("<b>Tipo Inicial:</b>", body_style), Paragraph(inc["init_type"], body_style), Paragraph("<b>Duración Total:</b>", body_style), Paragraph(inc["duracion_total_str"], body_bold)],
                [Paragraph("<b>Evolución:</b>", body_style), Paragraph(inc["evolution"], body_bold), Paragraph("<b>Tiempo Degradado:</b>", body_style), Paragraph(inc["duracion_deg_str"], body_style)],
                [Paragraph("<b>Inicio Observado:</b>", body_style), Paragraph(inc["start_time"], body_style), Paragraph("<b>Tiempo en Caída Total:</b>", body_style), Paragraph(inc["duracion_caida_str"], body_style)],
                [Paragraph("<b>Confirmación:</b>", body_style), Paragraph(inc["confirmation_time"], body_style), Paragraph("<b>Estado Final:</b>", body_style), Paragraph("RECUPERADO" if not inc["is_active"] else "ACTIVO", body_style)],
            ]
            t_meta = Table(meta_inc, colWidths=[1.3 * inch, 2.1 * inch, 1.4 * inch, 1.9 * inch])
            t_meta.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
                ('PADDING', (0, 0), (-1, -1), 4),
            ]))
            inc_flow.append(t_meta)
            inc_flow.append(Spacer(1, 6))

            # Matriz de sensores participantes
            s = inc.get("sensors", {})
            sens_text = (
                f"L0: {'✓' if s.get('l0') else '—'}   |   "
                f"L1: {'✓' if s.get('l1') else '—'}   |   "
                f"Fast.com: {'✓' if s.get('fast') else 'No ejecutado'}   |   "
                f"Ookla: {'✓' if s.get('ookla') else 'No ejecutado'}   |   "
                f"FSM: ✓"
            )
            inc_flow.append(Paragraph(f"<b>Matriz de Evidencia de Sensores:</b> {sens_text}", body_style))

            if inc.get("sensor_bypass_note"):
                inc_flow.append(Paragraph(f"<i>{inc['sensor_bypass_note']}</i>", alert_style))

            inc_flow.append(Spacer(1, 6))

            # Tabla de Evidencia Cronológica
            ev_table_rows = [
                [Paragraph("<b>Timestamp</b>", body_bold), Paragraph("<b>Fuente</b>", body_bold), Paragraph("<b>Símbolo</b>", body_bold), Paragraph("<b>Valor / Medición</b>", body_bold), Paragraph("<b>Estado FSM</b>", body_bold), Paragraph("<b>Observación</b>", body_bold)]
            ]

            for row in inc.get("evidence_table", []):
                ev_table_rows.append([
                    Paragraph(row["timestamp"], code_style),
                    Paragraph(row["fuente"], body_style),
                    Paragraph(f"<code>{row['simbolo']}</code>", code_style),
                    Paragraph(row["valor"], body_style),
                    Paragraph(row["estado_fsm"], body_style),
                    Paragraph(row["observacion"], body_style),
                ])

            t_ev = Table(ev_table_rows, colWidths=[1.4 * inch, 0.6 * inch, 0.6 * inch, 1.8 * inch, 0.9 * inch, 1.4 * inch], repeatRows=1)
            t_ev.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
                ('PADDING', (0, 0), (-1, -1), 3),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            inc_flow.append(t_ev)
            inc_flow.append(Spacer(1, 14))

            story.append(KeepTogether(inc_flow))

    # =========================================================================
    # 4. TIMELINE CONSOLIDADO
    # =========================================================================
    story.append(Paragraph("3. TIMELINE CONSOLIDADO DE EVENTOS", h1_style))
    story.append(Paragraph("Secuencia cronológica unificada de transiciones FSM y ejecuciones de prueba registradas.", body_style))
    story.append(Spacer(1, 6))

    timeline_items = data.get("timeline", [])
    if timeline_items:
        tl_rows = [[Paragraph("<b>Timestamp</b>", body_bold), Paragraph("<b>Fuente</b>", body_bold), Paragraph("<b>Evento / Transición Registrada</b>", body_bold)]]
        for t_item in timeline_items:
            tl_rows.append([
                Paragraph(t_item["timestamp"], code_style),
                Paragraph(t_item["source"], body_style),
                Paragraph(t_item["event"], body_style),
            ])
        t_tl = Table(tl_rows, colWidths=[1.5 * inch, 0.9 * inch, 4.3 * inch], repeatRows=1)
        t_tl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
            ('PADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(t_tl)
    else:
        story.append(Paragraph("<i>Sin eventos en el timeline.</i>", body_style))

    story.append(Spacer(1, 16))

    # =========================================================================
    # 5. GRÁFICOS VECTORIALES (REPORTLAB DRAWINGS)
    # =========================================================================
    story.append(Paragraph("4. GRÁFICOS TÉCNICOS VECTORIALES", h1_style))
    story.append(Paragraph("Visualizaciones vectoriales generadas a partir de la serie de tiempo almacenada.", body_style))
    story.append(Spacer(1, 8))

    # Gráfico 1: Timeline de Estados FSM
    story.append(Paragraph("<b>Gráfico 1 — Línea Temporal de Estados FSM</b>", h2_style))
    d1 = Drawing(450, 100)
    d1.add(Rect(0, 0, 450, 100, fillColor=colors.HexColor("#F8FAFC"), strokeColor=colors.HexColor("#CBD5E1")))
    # Renderizar niveles de estados FSM
    states_y = {"NORMAL": 20, "SOSPECHA": 40, "CONFIRMANDO": 60, "EVENTO": 80}
    for s_name, s_y in states_y.items():
        d1.add(String(10, s_y - 3, s_name, fontName="Helvetica-Bold", fontSize=7, fillColor=colors.HexColor("#64748B")))
        d1.add(Line(70, s_y, 440, s_y, strokeColor=colors.HexColor("#E2E8F0"), strokeWidth=0.5))

    # Trazar puntos FSM si existen
    raw_fsm = data.get("raw_fsm", [])
    if raw_fsm:
        pts = []
        step_x = 370.0 / max(1, len(raw_fsm) - 1)
        for idx, f_item in enumerate(raw_fsm):
            x_val = 70 + idx * step_x
            st = f_item.get("next_state", "NORMAL")
            y_val = states_y.get(st, 20)
            pts.append((x_val, y_val))
            color_p = colors.HexColor("#E11D48") if st == "EVENTO" else colors.HexColor("#65A30D")
            d1.add(Rect(x_val - 2, y_val - 2, 4, 4, fillColor=color_p, strokeColor=None))
        for i in range(len(pts) - 1):
            d1.add(Line(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1], strokeColor=colors.HexColor("#0284C7"), strokeWidth=1))
    else:
        d1.add(String(200, 45, "Línea continua en NORMAL (100% estable)", fontName="Helvetica", fontSize=8, fillColor=colors.HexColor("#64748B")))

    story.append(d1)
    story.append(Spacer(1, 14))

    # Gráfico 2: L1 Throughput vs Baseline
    story.append(Paragraph("<b>Gráfico 2 — Throughput L1 (Mbps) vs Baseline</b>", h2_style))
    d2 = Drawing(450, 110)
    d2.add(Rect(0, 0, 450, 110, fillColor=colors.HexColor("#F8FAFC"), strokeColor=colors.HexColor("#CBD5E1")))
    
    raw_l1 = data.get("raw_l1", [])
    if raw_l1:
        # Dibujar baseline y throughput
        mb_vals = [(r.get("throughput_mbps") or 0.0) for r in raw_l1] + [(r.get("baseline_mbps") or 0.0) for r in raw_l1] + [100.0]
        max_val = max(mb_vals) if mb_vals else 100.0
        step_x = 370.0 / max(1, len(raw_l1) - 1)
        
        # Ejes
        d2.add(Line(50, 15, 440, 15, strokeColor=colors.HexColor("#94A3B8")))
        d2.add(Line(50, 15, 50, 100, strokeColor=colors.HexColor("#94A3B8")))
        d2.add(String(5, 55, "Mbps", fontName="Helvetica-Bold", fontSize=7, fillColor=colors.HexColor("#64748B")))

        for i in range(len(raw_l1) - 1):
            x1 = 50 + i * step_x
            x2 = 50 + (i + 1) * step_x
            
            tp1 = raw_l1[i].get("throughput_mbps") or 0.0
            tp2 = raw_l1[i+1].get("throughput_mbps") or 0.0
            bs1 = raw_l1[i].get("baseline_mbps") or 0.0
            bs2 = raw_l1[i+1].get("baseline_mbps") or 0.0

            v1 = (tp1 / max_val) * 75 + 15
            v2 = (tp2 / max_val) * 75 + 15

            b1 = (bs1 / max_val) * 75 + 15
            b2 = (bs2 / max_val) * 75 + 15

            # Baseline punteado azul
            d2.add(Line(x1, b1, x2, b2, strokeColor=colors.HexColor("#0284C7"), strokeWidth=1, strokeDashArray=[2, 2]))
            # Throughput verde/rojo
            c_line = colors.HexColor("#E11D48") if raw_l1[i+1].get("is_degraded") else colors.HexColor("#65A30D")
            d2.add(Line(x1, v1, x2, v2, strokeColor=c_line, strokeWidth=1.5))

    else:
        d2.add(String(180, 50, "Sin lecturas L1 en el período", fontName="Helvetica", fontSize=8, fillColor=colors.HexColor("#64748B")))

    story.append(d2)
    story.append(Spacer(1, 16))

    # =========================================================================
    # 6. ANÁLISIS ESTADÍSTICO Y CALIDAD DE DATOS
    # =========================================================================
    story.append(Paragraph("5. ANÁLISIS ESTADÍSTICO Y CALIDAD DE DATOS", h1_style))
    
    stats = data.get("estadisticas", {})
    cob = data.get("cobertura", {})

    stats_table_data = [
        [Paragraph("<b>Indicador Estadístico</b>", body_bold), Paragraph("<b>Valor Calculado</b>", body_bold)],
        [Paragraph("Duración Mínima de Incidente", body_style), Paragraph(stats.get("dur_min", "—"), body_style)],
        [Paragraph("Duración Máxima de Incidente", body_style), Paragraph(stats.get("dur_max", "—"), body_style)],
        [Paragraph("Duración Promedio de Incidente", body_style), Paragraph(stats.get("dur_avg", "—"), body_style)],
        [Paragraph("Duración Mediana de Incidente", body_style), Paragraph(stats.get("dur_med", "—"), body_style)],
        [Paragraph("Porcentaje de Tiempo Afectado", body_style), Paragraph(stats.get("pct_afectado", "0.00%"), body_style)],
        [Paragraph("Porcentaje de Tiempo Normal", body_style), Paragraph(stats.get("pct_normal", "100.00%"), body_style)],
    ]
    t_stats = Table(stats_table_data, colWidths=[4.2 * inch, 2.5 * inch])
    t_stats.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t_stats)
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Calidad y Cobertura de la Observación</b>", h2_style))
    cob_table_data = [
        [Paragraph("<b>Métrica de Cobertura</b>", body_bold), Paragraph("<b>Conteo / Registro</b>", body_bold)],
        [Paragraph("Período Solicitado", body_style), Paragraph(cob.get("periodo_solicitado", "—"), body_style)],
        [Paragraph("Período Realmente Cubierto", body_style), Paragraph(cob.get("periodo_cubierto", "—"), body_style)],
        [Paragraph("Lecturas Sensor L0 (Conectividad)", body_style), Paragraph(str(cob.get("l0_count", 0)), body_style)],
        [Paragraph("Lecturas Sensor L1 (Micro-throughput)", body_style), Paragraph(str(cob.get("l1_count", 0)), body_style)],
        [Paragraph("Lecturas Sensor Fast.com", body_style), Paragraph(str(cob.get("fast_count", 0)), body_style)],
        [Paragraph("Ejecuciones Sensor Ookla (Oficial)", body_style), Paragraph(str(cob.get("ookla_count", 0)), body_style)],
        [Paragraph("Transiciones FSM Registradas", body_style), Paragraph(str(cob.get("fsm_count", 0)), body_style)],
        [Paragraph("Intervalos sin Datos (Gaps > 10 min)", body_style), Paragraph(str(cob.get("gaps_count", 0)), body_style)],
    ]
    t_cob = Table(cob_table_data, colWidths=[4.2 * inch, 2.5 * inch])
    t_cob.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t_cob)
    story.append(Spacer(1, 16))

    # =========================================================================
    # 7. METODOLOGÍA, ALCANCE Y LIMITACIONES DE LA EVIDENCIA
    # =========================================================================
    story.append(Paragraph("6. METODOLOGÍA Y ALCANCE DE LA EVIDENCIA", h1_style))
    
    meto_text = (
        "<b>METODOLOGÍA DE MONITOREO TRACEFLY V2:</b><br/>"
        "El sistema opera mediante cuatro sensores desacoplados y una Máquina de Estados Finitos (Mealy FSM V2):<br/>"
        "• <b>L0 — Conectividad:</b> Evalúa la presencia de enlace físico y alcance IP/DNS mediante pings periódicos.<br/>"
        "• <b>L1 — Micro-throughput:</b> Mide el rendimiento liviano continuo para detectar desviaciones respecto al baseline.<br/>"
        "• <b>Fast.com:</b> Sensor de confirmación intermedia ante sospechas de degradación.<br/>"
        "• <b>Ookla:</b> Confirmación pesada final de alta precisión.<br/>"
        "• <b>FSM Mealy V2:</b> Transita determinísticamente entre los estados <code>NORMAL</code>, <code>SOSPECHA</code>, "
        "<code>CONFIRMANDO</code> y <code>EVENTO</code> en respuesta a los símbolos de entrada <code>n</code> (normal), "
        "<code>a</code> (anomalía), <code>d</code> (desestimación), <code>cd</code> (degradación confirmada) y "
        "<code>co</code> (caída total)."
    )
    story.append(Paragraph(meto_text, body_style))
    story.append(Spacer(1, 10))

    disclaimer_text = (
        "<b>ALCANCE Y LIMITACIONES DE LA EVIDENCIA:</b><br/>"
        "Las mediciones corresponden exclusivamente a observaciones realizadas desde el punto donde se encuentra instalado el agente ControlInternet V2 / Tracefly V2.<br/>"
        "Los resultados permiten caracterizar objetivamente la disponibilidad y el rendimiento observados desde dicho punto de monitoreo.<br/>"
        "Los registros no determinan por sí solos qué componente específico de la infraestructura interna del proveedor es responsable de una incidencia. "
        "La identificación del origen requiere correlacionar las evidencias correspondientes a los niveles de diagnóstico disponibles."
    )
    disclaimer_table = Table([[Paragraph(disclaimer_text, body_style)]], colWidths=[6.7 * inch])
    disclaimer_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#CBD5E1')),
        ('PADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(disclaimer_table)

    # Construir PDF en memoria
    doc.build(story, canvasmaker=NumberedCanvas)
    pdf_bytes = buffer.getvalue()

    # Calcular Hash SHA-256 de los bytes finales del PDF
    sha256_hex = hashlib.sha256(pdf_bytes).hexdigest()
    filename_base = f"reporte_tecnico_{data.get('report_id', 'CI-REPORT')}"

    return pdf_bytes, sha256_hex, filename_base
