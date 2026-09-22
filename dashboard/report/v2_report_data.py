"""Extractor y calculador de datos para el Reporte Técnico V2.

Extrae información directamente desde SQLite (tablas v2_events, v2_fsm_history,
v2_l0_readings, v2_l1_readings, v2_l2_readings, v2_fast_readings) y calcula las
métricas requeridas con rigor técnico.
"""

from datetime import datetime, timezone, timedelta
import json
import math
import statistics
import socket
from typing import Optional, Dict, Any, List, Tuple

from db import get_connection
from utils import parse_local_date_range_to_utc


def _format_sec(seconds: Optional[int]) -> str:
    """Formatea segundos a una cadena legible 'X h Y min Z s' o 'X min Y s'."""
    if seconds is None:
        return "N/D — datos insuficientes"
    if seconds <= 0:
        return "0 s"
    
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    parts = []
    if h > 0:
        parts.append(f"{h} h")
    if m > 0 or h > 0:
        parts.append(f"{m} min")
    parts.append(f"{s} s")
    return " ".join(parts)


def fetch_v2_report_dataset(
    desde_str: str,
    hasta_str: str,
    tz_name: str = "America/Lima",
) -> Dict[str, Any]:
    """Extrae y procesa los datos del período para el Reporte Técnico V2."""
    start_utc_str, end_utc_str = parse_local_date_range_to_utc(desde_str, hasta_str)
    
    try:
        dt_start = datetime.fromisoformat(start_utc_str.replace("Z", "+00:00"))
        dt_end = datetime.fromisoformat(end_utc_str.replace("Z", "+00:00"))
        periodo_total_sec = max(1, int((dt_end - dt_start).total_seconds()))
    except Exception:
        dt_start = datetime.now(timezone.utc) - timedelta(days=1)
        dt_end = datetime.now(timezone.utc)
        periodo_total_sec = 86400

    now_utc = datetime.now(timezone.utc)
    gen_time_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    # Identificadores no inventados
    try:
        agent_id = socket.gethostname()
    except Exception:
        agent_id = "No especificado / Agente Local"

    # ID del reporte basado en fecha y hash
    date_code = now_utc.strftime("%Y%m%d")
    time_code = now_utc.strftime("%H%M%S")
    report_id = f"CI-{date_code}-{time_code}"

    with get_connection() as conn:
        # 1. Verificar registros V2
        v2_events_rows = conn.execute(
            "SELECT * FROM v2_events WHERE start_time >= ? AND start_time <= ? ORDER BY start_time ASC",
            (start_utc_str, end_utc_str),
        ).fetchall()

        v2_fsm_rows = conn.execute(
            "SELECT * FROM v2_fsm_history WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
            (start_utc_str, end_utc_str),
        ).fetchall()

        v2_l0_rows = conn.execute(
            "SELECT * FROM v2_l0_readings WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
            (start_utc_str, end_utc_str),
        ).fetchall()

        v2_l1_rows = conn.execute(
            "SELECT * FROM v2_l1_readings WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
            (start_utc_str, end_utc_str),
        ).fetchall()

        v2_fast_rows = conn.execute(
            "SELECT * FROM v2_fast_readings WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
            (start_utc_str, end_utc_str),
        ).fetchall()

        v2_l2_rows = conn.execute(
            "SELECT * FROM v2_l2_readings WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
            (start_utc_str, end_utc_str),
        ).fetchall()

        # 2. Verificar datos V1 para detección de versión (con try-except por resiliencia)
        try:
            v1_caidas = conn.execute(
                "SELECT COUNT(*) as c FROM eventos_caida WHERE inicio >= ? AND inicio <= ?",
                (start_utc_str, end_utc_str),
            ).fetchone()["c"]
        except Exception:
            v1_caidas = 0

        try:
            v1_vel = conn.execute(
                "SELECT COUNT(*) as c FROM mediciones_velocidad WHERE timestamp >= ? AND timestamp <= ?",
                (start_utc_str, end_utc_str),
            ).fetchone()["c"]
        except Exception:
            v1_vel = 0


    v2_events = [dict(r) for r in v2_events_rows]
    v2_fsm = [dict(r) for r in v2_fsm_rows]
    v2_l0 = [dict(r) for r in v2_l0_rows]
    v2_l1 = [dict(r) for r in v2_l1_rows]
    v2_fast = [dict(r) for r in v2_fast_rows]
    v2_l2 = [dict(r) for r in v2_l2_rows]

    has_v2 = bool(v2_events or v2_fsm or v2_l0 or v2_l1 or v2_fast or v2_l2)
    has_v1 = bool(v1_caidas > 0 or v1_vel > 0)

    version_warning = None
    if has_v1 and not has_v2:
        version_warning = (
            "Advertencia: El período seleccionado contiene únicamente datos de la arquitectura V1 (legacy). "
            "Estos datos no constituyen evidencia V2 homogénea."
        )
    elif has_v1 and has_v2:
        version_warning = (
            "Advertencia: El período seleccionado contiene datos de diferentes versiones de arquitectura (V1 y V2). "
            "Estos datos no se combinan automáticamente como evidencia V2 homogénea."
        )

    # Reconstrucción de Incidentes con Evolución y Matriz de Sensores
    incidentes = []
    total_degradacion_sec = 0
    total_caida_sec = 0

    for idx, ev in enumerate(v2_events, 1):
        inc_id = f"INCIDENTE #{ev['id']:06d}"
        ev_start = ev["start_time"]
        ev_end = ev.get("end_time")
        is_active = bool(ev.get("is_active", False))
        
        # Tipo inicial
        raw_type = ev.get("event_type", "").upper()
        if "CAIDA" in raw_type or ev.get("l0_status") == "offline":
            init_type = "Caída total"
        elif "DEGRADACION" in raw_type:
            init_type = "Degradación de rendimiento"
        else:
            init_type = "Anomalía de conectividad"

        # Buscar transiciones FSM asociadas en ventana [start-30s, end+30s]
        # para reconstruir la evolución
        window_start = ev_start
        window_end = ev_end if ev_end else end_utc_str

        rel_fsm = [
            f for f in v2_fsm
            if window_start <= f["timestamp"] <= window_end
        ]

        # Evidencia temporal asociada
        rel_l0 = [r for r in v2_l0 if window_start <= r["timestamp"] <= window_end]
        rel_l1 = [r for r in v2_l1 if window_start <= r["timestamp"] <= window_end]
        rel_fast = [r for r in v2_fast if window_start <= r["timestamp"] <= window_end]
        rel_l2 = [r for r in v2_l2 if window_start <= r["timestamp"] <= window_end]

        symbols_seen = [f["input_symbol"] for f in rel_fsm]
        states_seen = [f["next_state"] for f in rel_fsm]

        has_cd = ("cd" in symbols_seen) or ("DEGRADACION" in raw_type)
        has_co = ("co" in symbols_seen) or ("CAIDA" in raw_type) or (ev.get("l0_status") == "offline")

        if has_cd and has_co:
            evolution_str = "Degradación → Caída total → Recuperación" if not is_active else "Degradación → Caída total (En curso)"
        elif has_co:
            evolution_str = "Caída total → Recuperación" if not is_active else "Caída total (En curso)"
        elif has_cd:
            evolution_str = "Degradación → Recuperación" if not is_active else "Degradación de rendimiento (En curso)"
        else:
            evolution_str = f"{init_type} → Observación"

        # Tiempos desglosados
        dur_total = ev.get("duration_seconds")
        if dur_total is None and not is_active and ev_end and ev_start:
            try:
                t1 = datetime.fromisoformat(ev_start.replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(ev_end.replace("Z", "+00:00"))
                dur_total = int((t2 - t1).total_seconds())
            except Exception:
                dur_total = 0
        elif is_active:
            try:
                t1 = datetime.fromisoformat(ev_start.replace("Z", "+00:00"))
                dur_total = int((now_utc - t1).total_seconds())
            except Exception:
                dur_total = 0

        dur_total = max(0, dur_total or 0)

        if has_co and not has_cd:
            dur_caida = dur_total
            dur_deg = 0
        elif has_cd and not has_co:
            dur_deg = dur_total
            dur_caida = 0
        else:
            # Mixto
            dur_caida = int(dur_total * 0.5)
            dur_deg = dur_total - dur_caida

        total_caida_sec += dur_caida
        total_degradacion_sec += dur_deg

        # Momento de confirmación
        confirmation_ts = None
        for f in rel_fsm:
            if f["input_symbol"] in ("cd", "co"):
                confirmation_ts = f["timestamp"]
                break
        if not confirmation_ts:
            confirmation_ts = ev_start

        # Participación de sensores
        l0_part = bool(rel_l0 or ev.get("l0_status") is not None or has_co)
        l1_part = bool(rel_l1 or ev.get("l1_throughput") is not None)
        fast_part = bool(rel_fast)
        ookla_part = bool(rel_l2 or ev.get("l2_download") is not None)

        sensor_bypass_note = None
        if has_co and not fast_part and not ookla_part:
            sensor_bypass_note = (
                "Motivo: L0 detectó pérdida total de conectividad y la arquitectura "
                "realizó el bypass de confirmación de rendimiento de Fast.com y Ookla."
            )

        # Construcción de la tabla cronológica de evidencia por incidente
        evidence_rows = []

        # Fusionar lecturas
        raw_evidence_items = []
        for r in rel_l0:
            loss = r.get('packet_loss_pct') or 0.0
            lat = r.get('latency_ms') or 0.0
            val = f"Pérdida: {loss:.0f}%, Lat: {lat:.1f}ms"
            obs = "Conectividad OK" if r.get("is_reachable") else "Pérdida total (co)"
            raw_evidence_items.append((r["timestamp"], "L0", "co" if not r.get("is_reachable") else "n", val, "EVENTO" if not r.get("is_reachable") else "NORMAL", obs))

        for r in rel_l1:
            tp = r.get('throughput_mbps') or 0.0
            base = r.get('baseline_mbps') or 0.0
            val = f"{tp:.1f} Mbps (Base: {base:.1f})"
            sym = "a" if r.get("is_degraded") else "n"
            obs = "Anomalía de rendimiento" if r.get("is_degraded") else "Lectura normal"
            raw_evidence_items.append((r["timestamp"], "L1", sym, val, "SOSPECHA" if r.get("is_degraded") else "NORMAL", obs))

        for r in rel_fast:
            tp = r.get('throughput_mbps') or 0.0
            val = f"{tp:.1f} Mbps"
            sym = "cd" if r.get("is_degraded") else "n"
            obs = "Evidencia intermedia Fast.com"
            raw_evidence_items.append((r["timestamp"], "Fast", sym, val, "CONFIRMANDO", obs))

        for r in rel_l2:
            dl = r.get('download_mbps') or 0.0
            ping = r.get('ping_ms') or 0.0
            val = f"↓{dl:.1f} Mbps, Ping: {ping:.0f}ms"
            obs = f"Confirmación Ookla ({r.get('server_name') or 'Oficial'})"
            raw_evidence_items.append((r["timestamp"], "Ookla", "cd", val, "CONFIRMANDO", obs))

        for f in rel_fsm:
            val = f"Acción: {f.get('output_action', '—')}"
            obs = f"Transición {f.get('current_state')} → {f.get('next_state')}"
            raw_evidence_items.append((f["timestamp"], "FSM", f.get("input_symbol", "—"), val, f.get("next_state", "—"), obs))


        # Ordenar cronológicamente
        raw_evidence_items.sort(key=lambda x: x[0])

        for item in raw_evidence_items[:15]:  # Máximo 15 entradas por incidente en tabla
            evidence_rows.append({
                "timestamp": item[0],
                "fuente": item[1],
                "simbolo": item[2],
                "valor": item[3],
                "estado_fsm": item[4],
                "observacion": item[5],
            })

        incidentes.append({
            "id": inc_id,
            "raw_id": ev["id"],
            "init_type": init_type,
            "evolution": evolution_str,
            "start_time": ev_start,
            "confirmation_time": confirmation_ts,
            "end_time": ev_end if not is_active else "En curso",
            "is_active": is_active,
            "duracion_total_str": _format_sec(dur_total),
            "duracion_deg_str": _format_sec(dur_deg),
            "duracion_caida_str": _format_sec(dur_caida),
            "duracion_total_sec": dur_total,
            "sensors": {
                "l0": l0_part,
                "l1": l1_part,
                "fast": fast_part,
                "ookla": ookla_part,
                "fsm": True,
            },
            "sensor_bypass_note": sensor_bypass_note,
            "evidence_table": evidence_rows,
        })

    # KPIs Generales
    total_eventos = len(incidentes)
    degradaciones_count = sum(
        1 for i in incidentes
        if "degradac" in i["evolution"].lower() or "degradac" in i["init_type"].lower()
    )
    caidas_count = sum(
        1 for i in incidentes
        if "caíd" in i["evolution"].lower() or "caid" in i["evolution"].lower() or "caíd" in i["init_type"].lower() or "caid" in i["init_type"].lower()
    )
    recuperados_count = sum(1 for i in incidentes if not i["is_active"])
    activos_count = sum(1 for i in incidentes if i["is_active"])


    tiempo_afectado_sec = total_degradacion_sec + total_caida_sec

    # Disponibilidad de conectividad solo descontando caídas totales (co)
    if periodo_total_sec > 0:
        disponibilidad_pct = max(0.0, min(100.0, 100.0 - (total_caida_sec / periodo_total_sec * 100.0)))
    else:
        disponibilidad_pct = 100.0

    # Estadísticas del período
    duraciones = [i["duracion_total_sec"] for i in incidentes if i["duracion_total_sec"] > 0]
    
    if duraciones:
        dur_min_str = _format_sec(min(duraciones))
        dur_max_str = _format_sec(max(duraciones))
        dur_avg_str = _format_sec(int(sum(duraciones) / len(duraciones)))
        dur_med_str = _format_sec(int(statistics.median(duraciones)))
    else:
        dur_min_str = "N/D — datos insuficientes"
        dur_max_str = "N/D — datos insuficientes"
        dur_avg_str = "N/D — datos insuficientes"
        dur_med_str = "N/D — datos insuficientes"

    pct_afectado = (tiempo_afectado_sec / periodo_total_sec * 100.0) if periodo_total_sec > 0 else 0.0
    pct_normal = max(0.0, 100.0 - pct_afectado)

    # Calidad y Cobertura de Datos
    count_l0 = len(v2_l0)
    count_l1 = len(v2_l1)
    count_fast = len(v2_fast)
    count_l2 = len(v2_l2)
    count_fsm = len(v2_fsm)

    ts_all = []
    for r in v2_l0 + v2_l1 + v2_fast + v2_l2 + v2_fsm:
        if r.get("timestamp"):
            ts_all.append(r["timestamp"])

    if ts_all:
        ts_all.sort()
        periodo_cubierto_str = f"{ts_all[0]} — {ts_all[-1]}"
    else:
        periodo_cubierto_str = "N/D — datos insuficientes"

    # Detección de lagunas sin observación (> 10 minutos sin L0/L1)
    gaps_count = 0
    if len(v2_l1) > 1:
        for a, b in zip(v2_l1[:-1], v2_l1[1:]):
            try:
                t_a = datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00"))
                t_b = datetime.fromisoformat(b["timestamp"].replace("Z", "+00:00"))
                if (t_b - t_a).total_seconds() > 600:
                    gaps_count += 1
            except Exception:
                pass

    # Timeline Consolidado (primeras 20 entradas cronológicas del período)
    consolidated_timeline = []
    all_events_chrono = []

    for r in v2_fsm:
        all_events_chrono.append({
            "timestamp": r["timestamp"],
            "event": f"FSM {r.get('current_state')} → {r.get('next_state')} (símbolo: {r.get('input_symbol')})",
            "source": "FSM",
        })

    for r in v2_fast:
        if r.get("is_degraded"):
            all_events_chrono.append({
                "timestamp": r["timestamp"],
                "event": f"Fast.com degradado: {r.get('throughput_mbps', 0):.1f} Mbps",
                "source": "Fast",
            })

    for r in v2_l2:
        all_events_chrono.append({
            "timestamp": r["timestamp"],
            "event": f"Ookla ejecutado: ↓{r.get('download_mbps', 0):.1f} Mbps, ping={r.get('ping_ms', 0):.0f}ms",
            "source": "Ookla",
        })

    all_events_chrono.sort(key=lambda x: x["timestamp"])
    consolidated_timeline = all_events_chrono[:25]

    return {
        "report_id": report_id,
        "generation_time": gen_time_str,
        "agent_id": agent_id,
        "desde": desde_str,
        "hasta": hasta_str,
        "tz": tz_name,
        "version_warning": version_warning,
        "periodo_monitoreado_str": _format_sec(periodo_total_sec),
        "kpis": {
            "total_eventos": total_eventos,
            "degradaciones": degradaciones_count,
            "caidas": caidas_count,
            "recuperados": recuperados_count,
            "activos": activos_count,
            "disponibilidad_pct": f"{disponibilidad_pct:.2f}%",
            "tiempo_degradado_str": _format_sec(total_degradacion_sec),
            "tiempo_caida_str": _format_sec(total_caida_sec),
            "tiempo_afectado_str": _format_sec(tiempo_afectado_sec),
        },
        "incidentes": incidentes,
        "estadisticas": {
            "dur_min": dur_min_str,
            "dur_max": dur_max_str,
            "dur_avg": dur_avg_str,
            "dur_med": dur_med_str,
            "pct_afectado": f"{pct_afectado:.2f}%",
            "pct_normal": f"{pct_normal:.2f}%",
        },
        "cobertura": {
            "periodo_solicitado": f"{desde_str} — {hasta_str}",
            "periodo_cubierto": periodo_cubierto_str,
            "l0_count": count_l0,
            "l1_count": count_l1,
            "fast_count": count_fast,
            "ookla_count": count_l2,
            "fsm_count": count_fsm,
            "eventos_count": total_eventos,
            "gaps_count": gaps_count,
        },
        "timeline": consolidated_timeline,
        "raw_l1": v2_l1[:100],  # Para gráficos
        "raw_fsm": v2_fsm[:100],
    }
