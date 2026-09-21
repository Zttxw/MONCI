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

        # Última lectura L2
        last_l2 = conn.execute(
            "SELECT * FROM v2_l2_readings ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

    return {
        "engine_version": "V2.0",
        "current_state": last_fsm["next_state"] if last_fsm else "NORMAL",
        "active_event": active_event,
        "latest_fsm_transition": dict(last_fsm) if last_fsm else None,
        "latest_readings": {
            "l0": dict(last_l0) if last_l0 else None,
            "l1": dict(last_l1) if last_l1 else None,
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
    sensor: str = Query(default="l1", regex="^(l0|l1|l2)$"),
    limit: int = Query(default=50, le=500),
):
    """Retorna las lecturas históricas de un sensor específico (l0, l1, l2)."""
    table_name = f"v2_{sensor}_readings"
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
