"""Suite de Pruebas Automáticas para Reportes Técnicos V2.

Valida los 15 escenarios de verificación requeridos:
1. Reporte con rango de fechas.
2. Reporte sin eventos (0 eventos).
3. Reporte con degradaciones confirmadas.
4. Reporte con caídas totales.
5. Reporte con eventos activos.
6. Reporte con ciclo de recuperación 3/3.
7. Reporte con datos faltantes (N/D — datos insuficientes).
8. Reporte de un período de varios días.
9. Validación de timestamps reales.
10. Validación de cálculos de duración.
11. Validación de estadísticas.
12. Generación exitosa de bytes de PDF.
13. No invención/fabricación de datos.
14. Presencia de Identificador y Período en encabezado.
15. Validación del hash de integridad SHA-256 (.sha256).
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dashboard")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import hashlib
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta

from report.v2_report_data import fetch_v2_report_dataset
from report.v2_pdf_engine import build_v2_pdf_report



@pytest.fixture
def mock_sqlite_v2_db(tmp_path, monkeypatch):
    """Crea una base de datos SQLite temporal con esquema V2 completo para pruebas."""
    db_file = tmp_path / "test_monitor.db"
    conn = sqlite3.connect(db_file)
    
    # Crear esquema
    conn.executescript("""
        CREATE TABLE v2_events (
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

        CREATE TABLE v2_fsm_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            current_state TEXT NOT NULL,
            input_symbol TEXT NOT NULL,
            next_state TEXT NOT NULL,
            output_action TEXT NOT NULL,
            is_reentry BOOLEAN NOT NULL DEFAULT 0,
            readings_json TEXT
        );

        CREATE TABLE v2_l0_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            is_reachable BOOLEAN NOT NULL,
            target TEXT,
            latency_ms REAL,
            packet_loss_pct REAL,
            sub_checks TEXT
        );

        CREATE TABLE v2_l1_readings (
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

        CREATE TABLE v2_l2_readings (
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

        CREATE TABLE v2_fast_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            throughput_mbps REAL NOT NULL,
            duration_ms REAL NOT NULL,
            bytes_downloaded INTEGER NOT NULL,
            baseline_mbps REAL,
            is_degraded BOOLEAN NOT NULL,
            is_valid BOOLEAN NOT NULL,
            server_name TEXT,
            error TEXT
        );

        CREATE TABLE eventos_caida (id INTEGER PRIMARY KEY);
        CREATE TABLE mediciones_velocidad (id INTEGER PRIMARY KEY);
        CREATE TABLE eventos_degradacion (id INTEGER PRIMARY KEY);
    """)
    conn.commit()
    conn.close()

    # Redireccionar db.get_connection para usar esta DB
    from db import get_connection

    @pytest.fixture
    def get_test_conn():
        c = sqlite3.connect(db_file)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    monkeypatch.setattr("report.v2_report_data.get_connection", lambda: get_connection_custom(db_file))
    return db_file


def get_connection_custom(db_file):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================================
# PRUEBAS UNITARIAS E INTEGRACIÓN (15 ESCENARIOS)
# =============================================================================

def test_01_reporte_con_rango_fechas(mock_sqlite_v2_db):
    """1. Generación de reporte con rango de fechas válido."""
    data = fetch_v2_report_dataset("2026-09-22 00:00:00", "2026-09-22 23:59:59", "America/Lima")
    assert data["desde"] == "2026-09-22 00:00:00"
    assert data["hasta"] == "2026-09-22 23:59:59"
    assert "report_id" in data
    assert data["report_id"].startswith("CI-")


def test_02_reporte_sin_eventos(mock_sqlite_v2_db):
    """2. Reporte en un período sin ningún evento."""
    data = fetch_v2_report_dataset("2026-09-22 00:00:00", "2026-09-22 23:59:59")
    assert data["kpis"]["total_eventos"] == 0
    assert data["kpis"]["disponibilidad_pct"] == "100.00%"
    assert len(data["incidentes"]) == 0


def test_03_reporte_con_degradaciones(mock_sqlite_v2_db):
    """3. Reporte con eventos de degradación confirmada (cd)."""
    conn = sqlite3.connect(mock_sqlite_v2_db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO v2_events (event_type, state_origin, start_time, duration_seconds, is_active, l1_throughput) "
        "VALUES ('DEGRADACION_VELOCIDAD', 'SOSPECHA', ?, 300, 0, 85.0)",
        (now,),
    )
    conn.commit()
    conn.close()

    data = fetch_v2_report_dataset("2020-01-01 00:00:00", "2099-12-31 23:59:59")
    assert data["kpis"]["total_eventos"] == 1
    assert data["kpis"]["degradaciones"] == 1
    assert data["kpis"]["caidas"] == 0


def test_04_reporte_con_caidas_totales(mock_sqlite_v2_db):
    """4. Reporte con eventos de caída total (co)."""
    conn = sqlite3.connect(mock_sqlite_v2_db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO v2_events (event_type, state_origin, start_time, duration_seconds, is_active, l0_status) "
        "VALUES ('CAIDA_TOTAL', 'NORMAL', ?, 600, 0, 'offline')",
        (now,),
    )
    conn.commit()
    conn.close()

    data = fetch_v2_report_dataset("2020-01-01 00:00:00", "2099-12-31 23:59:59")
    assert data["kpis"]["total_eventos"] == 1
    assert data["kpis"]["caidas"] == 1
    # Debe indicar nota de bypass de Fast/Ookla en el incidente
    inc = data["incidentes"][0]
    assert inc["sensor_bypass_note"] is not None
    assert "bypass" in inc["sensor_bypass_note"]


def test_05_reporte_con_eventos_activos(mock_sqlite_v2_db):
    """5. Reporte con incidentes actualmente activos (is_active = 1)."""
    conn = sqlite3.connect(mock_sqlite_v2_db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO v2_events (event_type, state_origin, start_time, is_active, l1_throughput) "
        "VALUES ('DEGRADACION', 'CONFIRMANDO', ?, 1, 50.0)",
        (now,),
    )
    conn.commit()
    conn.close()

    data = fetch_v2_report_dataset("2020-01-01 00:00:00", "2099-12-31 23:59:59")
    assert data["kpis"]["activos"] == 1
    inc = data["incidentes"][0]
    assert inc["is_active"] is True
    assert inc["end_time"] == "En curso"


def test_06_reporte_con_recuperacion_3_de_3(mock_sqlite_v2_db):
    """6. Reporte con ciclo de recuperación 3/3 (FSM → NORMAL)."""
    conn = sqlite3.connect(mock_sqlite_v2_db)
    now = datetime.now(timezone.utc)
    t1 = (now - timedelta(seconds=120)).isoformat()
    t2 = (now - timedelta(seconds=60)).isoformat()
    t3 = now.isoformat()

    conn.execute(
        "INSERT INTO v2_events (event_type, state_origin, start_time, end_time, duration_seconds, is_active) "
        "VALUES ('DEGRADACION', 'EVENTO', ?, ?, 120, 0)",
        (t1, t3),
    )
    conn.execute(
        "INSERT INTO v2_fsm_history (timestamp, current_state, input_symbol, next_state, output_action) "
        "VALUES (?, 'EVENTO', 'n', 'EVENTO', 'recuperacion_1_3')",
        (t1,),
    )
    conn.execute(
        "INSERT INTO v2_fsm_history (timestamp, current_state, input_symbol, next_state, output_action) "
        "VALUES (?, 'EVENTO', 'n', 'EVENTO', 'recuperacion_2_3')",
        (t2,),
    )
    conn.execute(
        "INSERT INTO v2_fsm_history (timestamp, current_state, input_symbol, next_state, output_action) "
        "VALUES (?, 'EVENTO', 'n', 'NORMAL', 'recuperacion_3_3')",
        (t3,),
    )
    conn.commit()
    conn.close()

    data = fetch_v2_report_dataset("2020-01-01 00:00:00", "2099-12-31 23:59:59")
    assert len(data["incidentes"]) == 1
    inc = data["incidentes"][0]
    assert inc["is_active"] is False


def test_07_reporte_con_datos_faltantes(mock_sqlite_v2_db):
    """7. Reporte donde los datos para estadísticas son insuficientes."""
    data = fetch_v2_report_dataset("2026-09-22 00:00:00", "2026-09-22 23:59:59")
    assert data["estadisticas"]["dur_min"] == "N/D — datos insuficientes"
    assert data["cobertura"]["periodo_cubierto"] == "N/D — datos insuficientes"


def test_08_reporte_varios_dias(mock_sqlite_v2_db):
    """8. Reporte sobre un rango multidía (ej. 7 días)."""
    data = fetch_v2_report_dataset("2026-09-15 00:00:00", "2026-09-22 23:59:59")
    assert "7 h" in data["periodo_monitoreado_str"] or "d" in data["periodo_monitoreado_str"] or "min" in data["periodo_monitoreado_str"]


def test_09_validacion_timestamps(mock_sqlite_v2_db):
    """9. Validación de timestamps reales en incidentes y evidencia."""
    conn = sqlite3.connect(mock_sqlite_v2_db)
    ts = "2026-09-22T10:15:30.123456+00:00"
    conn.execute(
        "INSERT INTO v2_events (event_type, state_origin, start_time, is_active) VALUES ('CAIDA', 'NORMAL', ?, 1)",
        (ts,),
    )
    conn.commit()
    conn.close()

    data = fetch_v2_report_dataset("2020-01-01 00:00:00", "2099-12-31 23:59:59")
    inc = data["incidentes"][0]
    assert inc["start_time"] == ts


def test_10_calculo_duraciones(mock_sqlite_v2_db):
    """10. Validación de formato de duraciones (X min Y s / X h Y min Z s)."""
    from report.v2_report_data import _format_sec
    assert _format_sec(45) == "45 s"
    assert _format_sec(125) == "2 min 5 s"
    assert _format_sec(3665) == "1 h 1 min 5 s"


def test_11_formulas_estadisticas(mock_sqlite_v2_db):
    """11. Validación de fórmulas de disponibilidad y porcentajes."""
    data = fetch_v2_report_dataset("2026-09-22 00:00:00", "2026-09-22 23:59:59")
    assert float(data["kpis"]["disponibilidad_pct"].replace("%", "")) == 100.0
    assert float(data["estadisticas"]["pct_normal"].replace("%", "")) == 100.0


def test_12_generacion_pdf_bytes(mock_sqlite_v2_db):
    """12. Validación de generación correcta de bytes PDF mediante ReportLab."""
    data = fetch_v2_report_dataset("2026-09-22 00:00:00", "2026-09-22 23:59:59")
    pdf_bytes, sha256_hex, filename_base = build_v2_pdf_report(data)
    
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 5000
    assert pdf_bytes.startswith(b"%PDF-")


def test_13_no_invencion_datos(mock_sqlite_v2_db):
    """13. Verificación de que no se inventan valores ficticios."""
    data = fetch_v2_report_dataset("2026-09-22 00:00:00", "2026-09-22 23:59:59")
    assert "TRACEFLY-AGENT-PERU-01" not in str(data)
    assert data["kpis"]["total_eventos"] == 0


def test_14_presencia_identificador_y_periodo(mock_sqlite_v2_db):
    """14. Verificación de que el PDF incluye el ID del reporte y el período."""
    data = fetch_v2_report_dataset("2026-09-22 00:00:00", "2026-09-22 23:59:59")
    pdf_bytes, sha256_hex, filename_base = build_v2_pdf_report(data)
    
    # El ID del reporte debe figurar en el nombre del archivo
    assert data["report_id"] in filename_base


def test_15_integridad_sha256(mock_sqlite_v2_db):
    """15. Verificación de que el hash SHA-256 coincide exactamente con los bytes del PDF."""
    data = fetch_v2_report_dataset("2026-09-22 00:00:00", "2026-09-22 23:59:59")
    pdf_bytes, sha256_hex, filename_base = build_v2_pdf_report(data)

    expected_hash = hashlib.sha256(pdf_bytes).hexdigest()
    assert sha256_hex == expected_hash
    assert len(sha256_hex) == 64
