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
    """Historial de mediciones de velocidad oficial (Ookla L2 V2 con fallback a V1)."""
    start_utc, end_utc = parse_local_date_range_to_utc(desde, hasta)

    with get_connection() as conn:
        v2_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_l2_readings'"
        ).fetchone()
        if v2_check:
            rows_v2 = conn.execute(
                "SELECT * FROM v2_l2_readings "
                "WHERE timestamp >= ? AND timestamp <= ? "
                "ORDER BY timestamp ASC",
                (start_utc, end_utc),
            ).fetchall()
            if rows_v2:
                return [
                    MedicionVelocidad(
                        id=r["id"],
                        timestamp=r["timestamp"],
                        descarga_mbps=r["download_mbps"],
                        subida_mbps=r["upload_mbps"],
                        ping_ms=r["ping_ms"],
                        latencia_bajo_carga_ms=r["loaded_latency_ms"],
                    )
                    for r in rows_v2
                ]

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
    """Historial de mediciones del probe liviano (V2 L1 con fallback a V1)."""
    start_utc, end_utc = parse_local_date_range_to_utc(desde, hasta)

    with get_connection() as conn:
        v2_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_l1_readings'"
        ).fetchone()
        if v2_check:
            rows_v2 = conn.execute(
                "SELECT * FROM v2_l1_readings "
                "WHERE timestamp >= ? AND timestamp <= ? "
                "ORDER BY timestamp ASC",
                (start_utc, end_utc),
            ).fetchall()
            if rows_v2:
                return [
                    MedicionProbeLiviano(
                        id=r["id"],
                        timestamp=r["timestamp"],
                        mbps_aproximado=r["throughput_mbps"],
                        tiempo_respuesta_ms=r["total_time_ms"],
                        servidor="Cloudflare CDN",
                        dns_ms=r["dns_ms"],
                        tcp_connect_ms=r["tcp_ms"],
                        tls_ms=r["tls_ms"],
                        ttfb_ms=r["ttfb_ms"],
                        transfer_ms=r["transfer_ms"],
                        mbps_throughput=r["throughput_mbps"],
                        latencia_ms=r["tcp_ms"],
                        streams_usados=r["streams_used"],
                        muestra_valida=r["is_valid"],
                    )
                    for r in rows_v2
                ]

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
    """Historial de eventos de degradación de velocidad (V2 Events con fallback a V1)."""
    start_utc, end_utc = parse_local_date_range_to_utc(desde, hasta)

    with get_connection() as conn:
        v2_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_events'"
        ).fetchone()
        if v2_check:
            rows_v2 = conn.execute(
                "SELECT * FROM v2_events "
                "WHERE start_time >= ? AND start_time <= ? "
                "ORDER BY start_time DESC",
                (start_utc, end_utc),
            ).fetchall()
            if rows_v2:
                res = []
                for r in rows_v2:
                    d = dict(r)
                    ev_type = d.get("event_type", "DEGRADATION")
                    sev = "critica" if ev_type == "OUTAGE" else "degradada"
                    tp = d.get("l1_throughput") or d.get("l2_download") or 0.0
                    res.append(
                        EventoDegradacion(
                            id=d["id"],
                            severidad=sev,
                            fuente=d.get("state_origin", "V2 Engine"),
                            baseline_mbps=200.0,
                            velocidad_mbps=tp,
                            inicio=d["start_time"],
                            fin=d.get("end_time"),
                            duracion_segundos=d.get("duration_seconds"),
                        )
                    )
                return res

        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='eventos_degradacion'"
        ).fetchone()
        if not table_check:
            return []

        rows = conn.execute(
            "SELECT * FROM eventos_degradacion "
            "WHERE inicio >= ? AND inicio <= ? "
            "ORDER BY inicio DESC",
            (start_utc, end_utc),
        ).fetchall()

        res = []
        for row in rows:
            d = dict(row)
            # Para eventos históricos V1 donde fin es NULL pero V1 ya finalizó/desconectó hace horas
            if d.get("fin") is None and d.get("duracion_segundos") is None:
                d["duracion_segundos"] = 0  # Marcado como finalizado/congelado al desacoplar V1
            res.append(EventoDegradacion(**d))

    return res


