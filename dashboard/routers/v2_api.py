"""Router de API V2 para el Dashboard.

Expone endpoints JSON para inspeccionar el estado de la FSM Mealy,
historial de transiciones (audit trail), eventos V2 y lecturas de los sensores L0/L1/L2.
"""

from typing import Optional
from fastapi import APIRouter, Query

from db import (
    get_connection,
    get_v2_fsm_history,
    get_v2_events,
    get_active_v2_event,
    get_v2_fast_readings,
    get_v2_timeline_data,
)

router = APIRouter(prefix="/api/v2", tags=["v2"])


@router.get("/status")
async def get_v2_status():
    """Retorna el estado sintético actual de la arquitectura V2."""
    active_event = get_active_v2_event()
    
    with get_connection() as conn:
        # Última transición FSM
        last_fsm = conn.execute(
            "SELECT * FROM v2_fsm_history ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

        # Última lectura L0
        last_l0 = conn.execute(
            "SELECT * FROM v2_l0_readings ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

        # Última lectura L1
        last_l1 = conn.execute(
            "SELECT * FROM v2_l1_readings ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

        # Última lectura Fast.com
        last_fast = conn.execute(
            "SELECT * FROM v2_fast_readings ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

        # Última lectura L2 (Ookla)
        last_l2 = conn.execute(
            "SELECT * FROM v2_l2_readings ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

    current_state = last_fsm["next_state"] if last_fsm else "NORMAL"
    l1_interval = 1 if current_state == "SOSPECHA" else 5

    return {
        "engine_version": "V2.0",
        "current_state": current_state,
        "l1_adaptive_interval_s": l1_interval,
        "active_event": active_event,
        "latest_fsm_transition": dict(last_fsm) if last_fsm else None,
        "latest_readings": {
            "l0": dict(last_l0) if last_l0 else None,
            "l1": dict(last_l1) if last_l1 else None,
            "fast": dict(last_fast) if last_fast else None,
            "l2": dict(last_l2) if last_l2 else None,
        },
    }


@router.get("/fsm-history")
async def get_fsm_history(limit: int = Query(default=50, le=500)):
    """Retorna el historial de transiciones de la FSM (audit trail completo)."""
    return get_v2_fsm_history(limit=limit)


@router.get("/events")
async def get_events(limit: int = Query(default=50, le=200)):
    """Retorna los eventos generados por la arquitectura V2."""
    return get_v2_events(limit=limit)


@router.get("/readings")
async def get_readings(
    sensor: str = Query(default="l1", pattern="^(l0|l1|l2|fast)$"),
    limit: int = Query(default=50, le=500),
):
    """Retorna las lecturas históricas de un sensor específico (l0, l1, l2, fast)."""
    table_name = f"v2_{sensor}_readings"
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/timeline")
async def get_timeline(limit: int = Query(default=150, le=500)):
    """Retorna datos unificados y sincronizados para la Línea de Tiempo V2."""
    return get_v2_timeline_data(limit=limit)


@router.get("/sensors-status")
async def get_sensors_status():
    """Retorna el estado de los 4 sensores (L0, L1, Fast, Ookla) con motivos de ejecución."""
    with get_connection() as conn:
        last_fsm = conn.execute(
            "SELECT * FROM v2_fsm_history ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        last_l0 = conn.execute(
            "SELECT * FROM v2_l0_readings ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        last_l1 = conn.execute(
            "SELECT * FROM v2_l1_readings ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        last_fast = conn.execute(
            "SELECT * FROM v2_fast_readings ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        last_l2 = conn.execute(
            "SELECT * FROM v2_l2_readings ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

    current_state = last_fsm["next_state"] if last_fsm else "NORMAL"
    l1_interval = 1 if current_state == "SOSPECHA" else 5

    # Determinar estado y motivo de ejecución para Fast.com y Ookla
    fast_status = {
        "sensor": "FAST.COM",
        "last_reading": dict(last_fast) if last_fast else None,
        "is_executed": last_fast is not None,
        "reason": (
            "Ejecutado como filtro de confirmación intermedia"
            if current_state in ("SOSPECHA", "CONFIRMANDO", "EVENTO")
            else "No ejecutado — motivo: no necesario en estado NORMAL"
        ),
    }

    ookla_status = {
        "sensor": "OOKLA",
        "last_reading": dict(last_l2) if last_l2 else None,
        "is_executed": last_l2 is not None,
        "reason": (
            "Ejecutado como confirmación pesada final"
            if current_state in ("CONFIRMANDO", "EVENTO")
            else "No ejecutado — motivo: no necesario sin confirmación de Fast.com o falla L0"
        ),
    }

    return {
        "current_fsm_state": current_state,
        "l0": {
            "sensor": "L0 CONECTIVIDAD",
            "interval_seconds": 60,
            "last_reading": dict(last_l0) if last_l0 else None,
        },
        "l1": {
            "sensor": "L1 MICRO-THROUGHPUT",
            "interval_seconds": l1_interval,
            "last_reading": dict(last_l1) if last_l1 else None,
        },
        "fast": fast_status,
        "ookla": ookla_status,
    }


@router.get("/diagnostic")
async def get_diagnostic():
    """Ejecuta una inspección de diagnóstico del estado actual (CURRENT STATE DIAGNOSTIC)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with get_connection() as conn:
        last_l1 = conn.execute("SELECT * FROM v2_l1_readings ORDER BY timestamp DESC LIMIT 1").fetchone()
        last_l2 = conn.execute("SELECT * FROM v2_l2_readings ORDER BY timestamp DESC LIMIT 1").fetchone()
        active_events = conn.execute("SELECT * FROM v2_events WHERE is_active = 1").fetchall()
        all_v2_events = conn.execute("SELECT * FROM v2_events ORDER BY start_time DESC LIMIT 10").fetchall()

    l1_dict = dict(last_l1) if last_l1 else None
    l2_dict = dict(last_l2) if last_l2 else None

    # Frescura L1 (180s)
    l1_age = None
    l1_fresh = False
    if l1_dict and l1_dict.get("timestamp"):
        try:
            ts_l1 = datetime.fromisoformat(l1_dict["timestamp"])
            l1_age = (now - ts_l1).total_seconds()
            l1_fresh = l1_age <= 180
        except Exception:
            pass

    # Frescura L2 (86400s / 24h)
    l2_age = None
    l2_fresh = False
    if l2_dict and l2_dict.get("timestamp"):
        try:
            ts_l2 = datetime.fromisoformat(l2_dict["timestamp"])
            l2_age = (now - ts_l2).total_seconds()
            l2_fresh = l2_age <= 86400
        except Exception:
            pass

    return {
        "title": "CURRENT STATE DIAGNOSTIC",
        "now": now_iso,
        "latest_l1": {
            "timestamp": l1_dict.get("timestamp") if l1_dict else None,
            "throughput_mbps": l1_dict.get("throughput_mbps") if l1_dict else None,
            "baseline_mbps": l1_dict.get("baseline_mbps") if l1_dict else None,
            "is_fresh": l1_fresh,
            "age_seconds": round(l1_age, 1) if l1_age is not None else None,
        },
        "latest_official_l2": {
            "timestamp": l2_dict.get("timestamp") if l2_dict else None,
            "download_mbps": l2_dict.get("download_mbps") if l2_dict else None,
            "baseline_mbps": l2_dict.get("baseline_mbps") if l2_dict else None,
            "is_fresh": l2_fresh,
            "age_seconds": round(l2_age, 1) if l2_age is not None else None,
        },
        "active_v2_events": [dict(e) for e in active_events],
        "recent_v2_events": [dict(e) for e in all_v2_events],
        "dashboard_status_source": "v2_l1_readings & v2_l0_readings & v2_fsm_history",
        "dashboard_baseline_source": "v2_l1_readings (median of last 20 fresh readings)",
    }
