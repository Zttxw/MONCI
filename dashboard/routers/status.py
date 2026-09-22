"""Router de estado — GET / (dashboard HTML) y GET /api/status (JSON)."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response


from db import get_connection, get_metadata
from models import (
    EstadoDestino,
    EstadoDNS,
    MedicionVelocidad,
    MedicionProbeLiviano,
    ResumenEstado,
)

router = APIRouter()


def _get_destinos_monitoreados() -> list[str]:
    """Construye la lista de destinos, incluyendo isp_hop si fue detectado."""
    destinos = ["gateway"]
    isp_hop = get_metadata("isp_hop")
    if isp_hop:
        destinos.append("isp_hop")
    destinos.extend(["8.8.8.8", "1.1.1.1"])
    return destinos


def _get_estado_destinos() -> list[EstadoDestino]:
    """Determina el estado actual de cada destino basado en V2 L0 y eventos V2."""
    estados = []
    gateway_ip = get_metadata("gateway_ip")
    isp_hop_ip = get_metadata("isp_hop")
    destinos = _get_destinos_monitoreados()

    with get_connection() as conn:
        # 1. Intentar V2 L0
        v2_l0_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_l0_readings'"
        ).fetchone()
        if v2_l0_check:
            row_l0 = conn.execute("SELECT * FROM v2_l0_readings ORDER BY timestamp DESC LIMIT 1").fetchone()
            if row_l0:
                is_up = bool(row_l0["is_reachable"])
                ts = row_l0["timestamp"]
                for d in destinos:
                    estados.append(EstadoDestino(destino=d, estado="UP" if is_up else "DOWN", desde=ts))
                return estados

        # 2. Fallback a V1
        for destino in destinos:
            search_destinos = [destino]
            if destino == "gateway" and gateway_ip:
                search_destinos.append(gateway_ip)
            elif destino == "isp_hop" and isp_hop_ip:
                search_destinos = [isp_hop_ip]

            placeholders = ",".join("?" for _ in search_destinos)
            row = conn.execute(
                f"SELECT inicio FROM eventos_caida WHERE destino IN ({placeholders}) AND fin IS NULL ORDER BY inicio DESC LIMIT 1",
                search_destinos,
            ).fetchone()

            if row:
                estados.append(EstadoDestino(destino=destino, estado="DOWN", desde=row["inicio"]))
            else:
                last = conn.execute(
                    f"SELECT fin FROM eventos_caida WHERE destino IN ({placeholders}) AND fin IS NOT NULL ORDER BY fin DESC LIMIT 1",
                    search_destinos,
                ).fetchone()
                desde = last["fin"] if last else None
                estados.append(EstadoDestino(destino=destino, estado="UP", desde=desde))

    return estados


def _get_estado_dns() -> EstadoDNS | None:
    """Determina el estado actual de la resolución DNS."""
    with get_connection() as conn:
        v2_l0_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_l0_readings'"
        ).fetchone()
        if v2_l0_check:
            row_l0 = conn.execute("SELECT * FROM v2_l0_readings ORDER BY timestamp DESC LIMIT 1").fetchone()
            if row_l0:
                sub_checks = row_l0["sub_checks"] if "sub_checks" in row_l0.keys() else None
                is_dns_ok = True
                if sub_checks:
                    import json
                    try:
                        sc = json.loads(sub_checks) if isinstance(sub_checks, str) else sub_checks
                        if "dns_google.com" in sc:
                            is_dns_ok = sc["dns_google.com"].get("ok", True)
                    except Exception:
                        pass
                return EstadoDNS(
                    dominio="google.com",
                    servidor_dns="8.8.8.8",
                    estado="UP" if is_dns_ok else "DOWN",
                    desde=row_l0["timestamp"],
                )

        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='eventos_dns'"
        ).fetchone()
        if not table_check:
            return None

        row = conn.execute(
            "SELECT dominio, servidor_dns, inicio FROM eventos_dns WHERE fin IS NULL ORDER BY inicio DESC LIMIT 1"
        ).fetchone()

        if row:
            return EstadoDNS(
                dominio=row["dominio"],
                servidor_dns=row["servidor_dns"],
                estado="DOWN",
                desde=row["inicio"],
            )

        last = conn.execute(
            "SELECT dominio, servidor_dns, fin FROM eventos_dns WHERE fin IS NOT NULL ORDER BY fin DESC LIMIT 1"
        ).fetchone()

        if last:
            return EstadoDNS(
                dominio=last["dominio"],
                servidor_dns=last["servidor_dns"],
                estado="UP",
                desde=last["fin"],
            )

        return EstadoDNS(
            dominio="google.com",
            servidor_dns="8.8.8.8",
            estado="UP",
            desde=None,
        )


def _get_ultima_velocidad() -> MedicionVelocidad | None:
    """Obtiene la última medición de velocidad oficial (Ookla)."""
    with get_connection() as conn:
        v2_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_l2_readings'"
        ).fetchone()
        if v2_check:
            row_v2 = conn.execute("SELECT * FROM v2_l2_readings ORDER BY timestamp DESC LIMIT 1").fetchone()
            if row_v2:
                return MedicionVelocidad(
                    id=row_v2["id"],
                    timestamp=row_v2["timestamp"],
                    descarga_mbps=row_v2["download_mbps"],
                    subida_mbps=row_v2["upload_mbps"],
                    ping_ms=row_v2["ping_ms"],
                    latencia_bajo_carga_ms=row_v2["loaded_latency_ms"],
                )

        row = conn.execute("SELECT * FROM mediciones_velocidad ORDER BY timestamp DESC LIMIT 1").fetchone()
        if row:
            return MedicionVelocidad(**dict(row))
    return None


def _get_ultimo_probe_liviano() -> MedicionProbeLiviano | None:
    """Obtiene la última medición del probe liviano de velocidad."""
    with get_connection() as conn:
        v2_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_l1_readings'"
        ).fetchone()
        if v2_check:
            row_v2 = conn.execute("SELECT * FROM v2_l1_readings ORDER BY timestamp DESC LIMIT 1").fetchone()
            if row_v2:
                return MedicionProbeLiviano(
                    id=row_v2["id"],
                    timestamp=row_v2["timestamp"],
                    mbps_aproximado=row_v2["throughput_mbps"],
                    tiempo_respuesta_ms=row_v2["total_time_ms"],
                    servidor="Cloudflare CDN",
                    dns_ms=row_v2["dns_ms"],
                    tcp_connect_ms=row_v2["tcp_ms"],
                    tls_ms=row_v2["tls_ms"],
                    ttfb_ms=row_v2["ttfb_ms"],
                    transfer_ms=row_v2["transfer_ms"],
                    mbps_throughput=row_v2["throughput_mbps"],
                    latencia_ms=row_v2["tcp_ms"],
                    streams_usados=row_v2["streams_used"],
                    muestra_valida=row_v2["is_valid"],
                )

        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mediciones_probe_liviano'"
        ).fetchone()
        if not table_check:
            return None

        row = conn.execute("SELECT * FROM mediciones_probe_liviano ORDER BY timestamp DESC LIMIT 1").fetchone()
        if row:
            return MedicionProbeLiviano(**dict(row))
    return None


def _get_probe_liviano_stats() -> tuple[int, float | None]:
    """Retorna (count, baseline_mbps) del probe liviano V2/V1."""
    with get_connection() as conn:
        v2_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_l1_readings'"
        ).fetchone()
        if v2_check:
            count = conn.execute("SELECT COUNT(*) FROM v2_l1_readings").fetchone()[0]
            rows = conn.execute(
                "SELECT throughput_mbps FROM v2_l1_readings WHERE (is_valid = 1 OR is_valid IS NULL) AND throughput_mbps > 0 ORDER BY timestamp DESC LIMIT 20"
            ).fetchall()
            if len(rows) >= 5:
                import statistics
                vals = [r["throughput_mbps"] for r in rows]
                return count, statistics.median(vals)

        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mediciones_probe_liviano'"
        ).fetchone()
        if not table_check:
            return 0, None
        count = conn.execute("SELECT COUNT(*) FROM mediciones_probe_liviano").fetchone()[0]
        if count < 5:
            return count, None
        rows = conn.execute("SELECT mbps_aproximado FROM mediciones_probe_liviano ORDER BY timestamp DESC LIMIT 20").fetchall()
        if not rows:
            return count, None
        avg = sum(r["mbps_aproximado"] for r in rows) / len(rows)
        return count, avg


@router.get("/api/status", response_model=ResumenEstado)
async def api_status():
    """Estado actual en formato JSON (alineado con la FSM Mealy V2)."""
    count, baseline = _get_probe_liviano_stats()

    fsm_state = "NORMAL"
    recovery_counter = 0
    recovery_k = 3
    input_symbol = None
    active_event = None

    with get_connection() as conn:
        fsm_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_fsm_history'"
        ).fetchone()
        if fsm_check:
            last_fsm = conn.execute(
                "SELECT * FROM v2_fsm_history ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if last_fsm:
                fsm_state = last_fsm["next_state"]
                input_symbol = last_fsm["input_symbol"]
                readings_json = last_fsm["readings_json"]
                if readings_json:
                    import json
                    try:
                        rj = json.loads(readings_json) if isinstance(readings_json, str) else readings_json
                        if "fsm" in rj:
                            recovery_counter = rj["fsm"].get("recovery_counter", 0)
                            recovery_k = rj["fsm"].get("recovery_k", 3)
                    except Exception:
                        pass

        ev_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_events'"
        ).fetchone()
        if ev_check:
            row_ev = conn.execute(
                "SELECT * FROM v2_events WHERE is_active = 1 ORDER BY start_time DESC LIMIT 1"
            ).fetchone()
            if row_ev:
                active_event = dict(row_ev)

    return ResumenEstado(
        destinos=_get_estado_destinos(),
        ultima_velocidad=_get_ultima_velocidad(),
        ultimo_probe_liviano=_get_ultimo_probe_liviano(),
        estado_dns=_get_estado_dns(),
        gateway_ip=get_metadata("gateway_ip"),
        isp_hop_ip=get_metadata("isp_hop"),
        probe_liviano_count=count,
        probe_liviano_baseline=baseline,
        fsm_state=fsm_state,
        recovery_counter=recovery_counter,
        recovery_k=recovery_k,
        input_symbol=input_symbol,
        active_event=active_event,
    )


@router.get("/", response_class=HTMLResponse)
async def dashboard_page():
    """Dashboard visual completo Tracefly V2 (Ultraminimalista, 6 Vistas, 0 Emojis)."""
    return DASHBOARD_HTML


@router.get("/favicon.ico", include_in_schema=False)
async def favicon_page():
    """Favicon SVG para evitar errores 404 en el navegador."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" fill="#C2EA00"/></svg>'
    return Response(content=svg, media_type="image/svg+xml")



# ---------------------------------------------------------------------------
# Dashboard HTML SPA Ultraminimalista V2 (0 Emojis)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TRACEFLY V2 — Observación & FSM Mealy</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  :root {
    /* 1. Fondos estructurales */
    --bg-main: #0F1115;
    --bg-module: #1B1D22;
    --bg-card-hover: #24272E;
    --border-color: #2A2D35;

    /* 2. Acento de Identidad Tracefly V2 (Volt / Lime Neón) */
    --brand-accent: #C2EA00;
    --brand-accent-faint: rgba(194, 234, 0, 0.08);
    --brand-accent-glow: rgba(194, 234, 0, 0.15);

    /* 3. Paleta Curada: Cyber Graphite & Volt Accent (Sin saturación de semáforo) */
    --color-normal: #C2EA00;
    --color-normal-bg: rgba(194, 234, 0, 0.08);
    --color-degraded: #7E22CE;
    --color-degraded-bg: rgba(126, 34, 206, 0.08);
    --color-outage: #F43F5E;
    --color-outage-bg: rgba(244, 63, 94, 0.08);

    /* 4. Sensores */
    --sensor-l0: #C2EA00;
    --sensor-l1: #7E22CE;
    --sensor-fast: #38BDF8;
    --sensor-ookla: #F43F5E;

    /* 5. Tipografía */
    --text-important: #FFFFFF;
    --text-muted: #8F94A0;
    --text-faint: #5A5E6B;

    --font-mono: 'IBM Plex Mono', monospace;
    --font-sans: 'Public Sans', sans-serif;
  }

  /* Inputs & Form Controls para Reportes V2 */
  input[type="datetime-local"], select {
    color-scheme: dark;
    background-color: #14161B !important;
    border: 1px solid #2F333E !important;
    color: #FFFFFF !important;
    padding: 10px 12px !important;
    border-radius: 6px !important;
    font-family: var(--font-mono) !important;
    font-size: 12px !important;
    outline: none !important;
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
  }

  /* Visibilidad en alta resolución del ícono de calendario en Chrome/Firefox */
  input[type="datetime-local"]::-webkit-calendar-picker-indicator {
    filter: invert(0.85) sepia(1) hue-rotate(30deg) saturate(6);
    cursor: pointer;
    opacity: 0.9;
    transition: transform 0.15s ease, opacity 0.15s ease;
  }
  input[type="datetime-local"]::-webkit-calendar-picker-indicator:hover {
    transform: scale(1.15);
    opacity: 1;
  }

  select option {
    background-color: #1B1D22 !important;
    color: #FFFFFF !important;
    padding: 8px !important;
  }

  input[type="datetime-local"]:focus, select:focus {
    border-color: var(--brand-accent) !important;
    box-shadow: 0 0 10px rgba(194, 234, 0, 0.25) !important;
  }

  .filter-btn {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    padding: 8px 14px;
    border-radius: 6px;
    background: #14161B;
    border: 1px solid #2F333E;
    color: #FFFFFF;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .filter-btn:hover {
    border-color: var(--brand-accent);
    color: var(--brand-accent);
    background: #1F222A;
    box-shadow: 0 0 10px rgba(194, 234, 0, 0.15);
  }

  .badge-subtle-volt {
    background: rgba(194, 234, 0, 0.12);
    color: #C2EA00;
    border: 1px solid rgba(194, 234, 0, 0.3);
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 11px;
  }
  .badge-subtle-purple {
    background: rgba(168, 85, 247, 0.15);
    color: #D8B4FE;
    border: 1px solid rgba(168, 85, 247, 0.35);
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 11px;
  }
  .badge-subtle-red {
    background: rgba(244, 63, 94, 0.15);
    color: #FDA4AF;
    border: 1px solid rgba(244, 63, 94, 0.35);
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 11px;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }



  body {
    background: var(--bg-main);
    color: var(--text-important);
    font-family: var(--font-sans);
    line-height: 1.4;
    height: 100vh;
    overflow: hidden;
  }

  .app-container {
    display: grid;
    grid-template-columns: 240px 1fr;
    height: 100vh;
  }

  /* Sidebar */
  .sidebar {
    background: var(--bg-module);
    border-right: 1px solid var(--border-color);
    display: flex;
    flex-direction: column;
    padding: 20px 16px;
  }

  .sidebar-header {
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border-color);
  }

  .brand-title {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--text-important);
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .brand-accent-text {
    color: var(--brand-accent);
  }

  .brand-badge {
    background: var(--brand-accent-faint);
    color: var(--brand-accent);
    border: 1px solid rgba(207, 240, 33, 0.3);
    font-size: 10px;
    padding: 1px 5px;
    border-radius: 4px;
    font-weight: 700;
  }

  .brand-sub {
    font-size: 10px;
    font-weight: 600;
    color: var(--text-muted);
    letter-spacing: 0.04em;
    margin-top: 4px;
    text-transform: uppercase;
  }

  .nav-menu {
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex: 1;
  }

  .nav-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    color: #FFFFFF;
    cursor: pointer;
    transition: all 0.15s ease;
    border: 1px solid transparent;
    text-decoration: none;
  }

  .nav-item:hover {
    color: #FFFFFF;
    background: var(--bg-card-hover);
  }

  .nav-item.active {
    color: #FFFFFF;
    background: var(--bg-card-hover);
    border-color: rgba(207, 240, 33, 0.3);
    box-shadow: 0 0 10px rgba(207, 240, 33, 0.05);
  }

  .nav-item.active .nav-num {
    color: var(--brand-accent);
  }

  .nav-num {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--brand-accent);
    font-weight: 700;
  }

  .nav-icon {
    width: 16px;
    height: 16px;
    stroke: var(--brand-accent);
    fill: none;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
    flex-shrink: 0;
    transition: transform 0.2s ease, stroke 0.2s ease;
  }

  .nav-item:hover .nav-icon {
    transform: scale(1.15);
    stroke: #FFFFFF;
  }

  .nav-item.active .nav-icon {
    stroke: var(--brand-accent);
    filter: drop-shadow(0 0 4px rgba(207, 240, 33, 0.4));
  }

  .sidebar-footer {
    padding-top: 16px;
    border-top: 1px solid var(--border-color);
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--text-faint);
  }

  /* Main Content */
  .content-area {
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow-y: auto;
    background: var(--bg-main);
  }

  .topbar {
    height: 56px;
    border-bottom: 1px solid var(--border-color);
    padding: 0 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--bg-module);
  }

  .topbar-title {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
  }

  .status-badge {
    display: flex;
    align-items: center;
    gap: 8px;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 4px;
    background: var(--bg-main);
    border: 1px solid var(--border-color);
  }

  .status-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--color-normal);
  }

  .view-container {
    padding: 24px;
    flex: 1;
    display: none;
  }

  .view-container.active {
    display: block;
  }

  /* Cards & Layout Utilities */
  .card {
    background: var(--bg-module);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 16px;
    margin-bottom: 16px;
    transition: all 0.15s ease;
  }

  .card:hover {
    background: var(--bg-card-hover);
  }

  .grid-3 {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
    margin-bottom: 16px;
  }

  .grid-2 {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 16px;
    margin-bottom: 16px;
  }

  .grid-4 {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin-bottom: 16px;
  }

  .card-label {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    color: var(--text-muted);
    letter-spacing: 0.05em;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .card-value {
    font-family: var(--font-mono);
    font-size: 22px;
    font-weight: 700;
    color: var(--text-important);
  }

  .card-sub {
    font-size: 11px;
    color: var(--text-faint);
    margin-top: 4px;
  }

  /* FSM Banner (Visualización sobria ultraminimalista sin líneas laterales) */
  .fsm-banner {
    padding: 20px;
    border-radius: 6px;
    background: var(--bg-module);
    border: 1px solid var(--border-color);
    margin-bottom: 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    transition: all 0.2s ease;
  }

  .fsm-banner.state-NORMAL {
    border-color: rgba(0, 240, 255, 0.3);
  }
  .fsm-banner.state-SOSPECHA, .fsm-banner.state-CONFIRMANDO, .fsm-banner.state-DEGRADACION {
    border-color: rgba(168, 85, 247, 0.3);
  }
  .fsm-banner.state-EVENTO, .fsm-banner.state-CAIDA {
    border-color: rgba(255, 0, 85, 0.3);
  }
    border-left-color: var(--color-degraded);
  }
  .fsm-banner.state-EVENTO, .fsm-banner.state-CAIDA {
    border-left-color: var(--color-outage);
  }

  .fsm-state-header {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .fsm-state-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--color-normal);
  }

  .fsm-banner.state-NORMAL .fsm-state-dot { background: var(--color-normal); }
  .fsm-banner.state-SOSPECHA .fsm-state-dot, .fsm-banner.state-CONFIRMANDO .fsm-state-dot, .fsm-banner.state-DEGRADACION .fsm-state-dot { background: var(--color-degraded); }
  .fsm-banner.state-EVENTO .fsm-state-dot, .fsm-banner.state-CAIDA .fsm-state-dot { background: var(--color-outage); }

  .fsm-state-title {
    font-family: var(--font-mono);
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 0.02em;
    color: var(--text-important);
  }

  .fsm-state-desc {
    font-size: 13px;
    color: var(--text-muted);
    margin-top: 4px;
  }

  .fsm-meta-group {
    display: flex;
    gap: 24px;
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .fsm-meta-item {
    text-align: right;
  }

  .fsm-meta-val {
    font-weight: 700;
    color: var(--text-important);
    margin-top: 2px;
  }

  /* Badges & Tags — Ultraminimalistas (Sin cuadros traseros, solo texto de color puro) */
  .badge-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 0 !important;
    border-radius: 0 !important;
    font-size: 11px;
    font-weight: 700;
    font-family: var(--font-mono);
    text-transform: uppercase;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
  }

  .tag-normal {
    color: var(--color-normal) !important;
    background: transparent !important;
    border: none !important;
  }
  .tag-degraded {
    color: var(--color-degraded) !important;
    background: transparent !important;
    border: none !important;
  }
  .tag-outage {
    color: var(--color-outage) !important;
    background: transparent !important;
    border: none !important;
  }
  .tag-brand {
    color: var(--brand-accent) !important;
    background: transparent !important;
    border: none !important;
  }

  /* Sensor Identifiers Badges */
  .sensor-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
  }

  .sensor-dot-l0 { color: var(--sensor-l0); }
  .sensor-dot-l1 { color: var(--sensor-l1); }
  .sensor-dot-fast { color: var(--sensor-fast); }
  .sensor-dot-ookla { color: var(--sensor-ookla); }

  /* Data Table */
  .terminal-table {
    width: 100%;
    border-collapse: collapse;
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .terminal-table th {
    text-align: left;
    padding: 10px 12px;
    background: var(--bg-main);
    border-bottom: 1px solid var(--border-color);
    color: var(--text-muted);
    font-weight: 600;
  }

  .terminal-table td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border-color);
    color: var(--text-important);
  }

  .terminal-table tr:hover {
    background: var(--bg-card-hover);
  }

  /* FSM Graph Visualizer (100% Unified Vector Architecture & Animations) */
  .fsm-graph-wrapper {
    position: relative;
    width: 100%;
    height: 480px;
    background: var(--bg-main);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    margin-top: 10px;
    overflow: hidden;
  }

  .fsm-svg-canvas {
    width: 100%;
    height: 100%;
    display: block;
  }

  .fsm-svg-canvas .fsm-node rect {
    fill: var(--bg-module);
    stroke: var(--border-color);
    stroke-width: 1.5px;
    transition: all 0.25s ease;
  }

  .fsm-svg-canvas .fsm-node:hover rect {
    fill: var(--bg-card-hover);
  }

  .fsm-svg-canvas .fsm-node.active rect {
    fill: var(--bg-card-hover);
    stroke-width: 2.5px;
  }

  /* Estilos estáticos e intuitivos para nodos FSM sin sobrecarga de animaciones */
  .fsm-svg-canvas .fsm-node rect {
    fill: #16171A;
    stroke: var(--border-color);
    stroke-width: 2px;
    transition: all 0.2s ease;
  }

  .fsm-svg-canvas .fsm-node.active.node-NORMAL rect {
    stroke: var(--color-normal);
    stroke-width: 2.5px;
    fill: rgba(0, 240, 255, 0.08);
    filter: drop-shadow(0 0 8px rgba(0, 240, 255, 0.35));
  }

  .fsm-svg-canvas .fsm-node.active.node-SOSPECHA rect {
    stroke: var(--color-degraded);
    stroke-width: 2.5px;
    fill: rgba(168, 85, 247, 0.08);
    filter: drop-shadow(0 0 8px rgba(168, 85, 247, 0.35));
  }

  .fsm-svg-canvas .fsm-node.active.node-CONFIRMANDO rect {
    stroke: var(--color-degraded);
    stroke-width: 2.5px;
    fill: rgba(168, 85, 247, 0.08);
    filter: drop-shadow(0 0 8px rgba(168, 85, 247, 0.35));
  }

  .fsm-svg-canvas .fsm-node.active.node-EVENTO rect {
    stroke: var(--color-outage);
    stroke-width: 2.5px;
    fill: rgba(255, 0, 85, 0.08);
    filter: drop-shadow(0 0 8px rgba(255, 0, 85, 0.35));
  }

  /* Resaltado estático limpio para rutas de transición activas */
  .fsm-path-active {
    stroke-width: 2.5px !important;
    opacity: 1 !important;
  }

  .node-title {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 700;
  }

  .node-sub {
    font-size: 10px;
    color: var(--text-muted);
    margin-top: 4px;
    font-family: var(--font-mono);
  }

  .fsm-connector {
    flex: 1;
    height: 2px;
    background: var(--border-color);
    position: relative;
    margin: 0 8px;
  }

  .connector-label {
    position: absolute;
    top: -18px;
    width: 100%;
    text-align: center;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--text-muted);
  }

  /* Incident Cards */
  .incident-card {
    border: 1px solid var(--border-color);
    border-left: 4px solid var(--color-outage);
    background: var(--bg-module);
    border-radius: 6px;
    padding: 16px;
    margin-bottom: 12px;
  }

  .incident-card.recovered {
    border-left-color: var(--color-normal);
  }

  .incident-header {
    display: flex;
    justify-content: space-between;
    margin-bottom: 12px;
  }

  .incident-id {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 700;
  }

  .incident-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .incident-prop-label {
    color: var(--text-muted);
    font-size: 10px;
    margin-bottom: 2px;
  }

  .filter-bar {
    display: flex;
    gap: 8px;
    margin-bottom: 16px;
  }

  .filter-btn {
    background: var(--bg-module);
    border: 1px solid var(--border-color);
    color: var(--text-muted);
    padding: 6px 14px;
    border-radius: 4px;
    font-family: var(--font-mono);
    font-size: 11px;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .filter-btn:hover {
    background: var(--bg-card-hover);
    color: var(--text-important);
  }

  .filter-btn.active {
    color: var(--brand-accent);
    border-color: var(--brand-accent);
    background: var(--brand-accent-faint);
  }

  .chart-container {
    height: 350px;
    position: relative;
  }
</style>
</head>
<body>

<div class="app-container">
  <!-- Sidebar Navigation -->
  <aside class="sidebar">
    <div class="sidebar-header">
      <div class="brand-title">
        TRACE<span class="brand-accent-text">FLY</span>
        <span class="brand-badge">V2</span>
      </div>
      <div class="brand-sub">Arquitectura Mealy FSM</div>
    </div>
    
    <nav class="nav-menu">
      <a class="nav-item active" onclick="switchView('dashboard', this)">
        <svg class="nav-icon" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>
        <span>Dashboard</span>
      </a>
      <a class="nav-item" onclick="switchView('fsm', this)">
        <svg class="nav-icon" viewBox="0 0 24 24"><circle cx="6" cy="6" r="3"/><circle cx="18" cy="12" r="3"/><circle cx="6" cy="18" r="3"/><path d="M9 6h4a3 3 0 0 1 3 3v0"/><path d="M9 18h4a3 3 0 0 0 3-3v0"/></svg>
        <span>Estado FSM</span>
      </a>
      <a class="nav-item" onclick="switchView('sensors', this)">
        <svg class="nav-icon" viewBox="0 0 24 24"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
        <span>Sensores</span>
      </a>
      <a class="nav-item" onclick="switchView('events', this)">
        <svg class="nav-icon" viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
        <span>Eventos</span>
      </a>
      <a class="nav-item" onclick="switchView('timeline', this)">
        <svg class="nav-icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></svg>
        <span>Línea de tiempo</span>
      </a>
      <a class="nav-item" onclick="switchView('reports', this)">
        <svg class="nav-icon" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/></svg>
        <span>Reportes</span>
      </a>
    </nav>

    <div class="sidebar-footer">
      <div>ENGINE: MEALY FSM V2.0</div>
      <div style="margin-top:4px; color:var(--text-faint);" id="sync-time">SYNC: --:--:--</div>
    </div>
  </aside>

  <!-- Main Content -->
  <main class="content-area">
    <header class="topbar">
      <div class="topbar-title" id="view-title">DASHBOARD OPERATIVO</div>
      <div class="status-badge">
        <span class="status-dot" id="status-dot"></span>
        <span id="system-status-text">MONITOREANDO</span>
      </div>
    </header>

    <!-- Vista 1 — Dashboard -->
    <div id="view-dashboard" class="view-container active">
      <div class="fsm-banner state-NORMAL" id="fsm-banner">
        <div>
          <div class="fsm-state-header">
            <span class="fsm-state-dot"></span>
            <div class="fsm-state-title" id="banner-state-title">ESTADO: NORMAL</div>
          </div>
          <div class="fsm-state-desc" id="banner-state-desc">Internet estable. Micro-throughput dentro de baseline.</div>
        </div>
        <div class="fsm-meta-group">
          <div class="fsm-meta-item">
            <div class="card-label">Última medición</div>
            <div class="fsm-meta-val" id="banner-last-ts">--:--:--</div>
          </div>
          <div class="fsm-meta-item">
            <div class="card-label">Tiempo en estado</div>
            <div class="fsm-meta-val" id="banner-state-time">00:00:00</div>
          </div>
        </div>
      </div>

      <div class="grid-3">
        <div class="card">
          <div class="card-label">
            <span>Conectividad</span>
            <span class="sensor-badge"><span class="sensor-dot-l0">◉</span> L0</span>
          </div>
          <div class="card-value" id="dash-l0-val">-- ms</div>
          <div class="card-sub" id="dash-l0-sub">Target: Gateway / DNS</div>
        </div>
        <div class="card">
          <div class="card-label">
            <span>Micro-throughput</span>
            <span class="sensor-badge"><span class="sensor-dot-l1">◉</span> L1</span>
          </div>
          <div class="card-value" id="dash-l1-val">-- Mbps</div>
          <div class="card-sub" id="dash-l1-sub">Intervalo actual: 5 s</div>
        </div>
        <div class="card">
          <div class="card-label">
            <span>Estado FSM Mealy</span>
            <span class="badge-tag tag-brand">FSM V2</span>
          </div>
          <div class="card-value" id="dash-fsm-val">NORMAL</div>
          <div class="card-sub" id="dash-fsm-sub">Acción: Muestreo 5s</div>
        </div>
      </div>

      <div class="card">
        <div class="card-label" style="margin-bottom:12px;">Actividad reciente (Audit Trail En Tiempo Real)</div>
        <table class="terminal-table">
          <thead>
            <tr>
              <th>HORA</th>
              <th>ORIGEN</th>
              <th>SÍMBOLO / ESTADO</th>
              <th>VALOR REGISTRADO</th>
              <th>DETALLE / TRANSICIÓN</th>
            </tr>
          </thead>
          <tbody id="dash-activity-body">
            <tr><td colspan="5" style="color:var(--text-muted);">Cargando telemetría...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Vista 2 — Estado FSM -->
    <div id="view-fsm" class="view-container">
      <div class="card">
        <div class="card-label">
          <span>Grafo de Transición Mealy FSM V2 (Caminos Adaptativos & Bypass)</span>
          <span class="badge-tag tag-brand">ARQUITECTURA NON-LINEAR</span>
        </div>
        
        <div class="fsm-graph-wrapper">
          <svg class="fsm-svg-canvas" viewBox="0 0 800 500" preserveAspectRatio="xMidYMid meet">
            <defs>
              <marker id="arrow-volt" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#C2EA00" />
              </marker>
              <marker id="arrow-violet" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#7E22CE" />
              </marker>
              <marker id="arrow-crimson" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#F43F5E" />
              </marker>
            </defs>

            <!-- CAPA 1: RUTAS Y LÍNEAS DE TRANSICIÓN SVG -->
            <!-- 1. NORMAL loop (n: operación normal 5s) -->
            <path id="path-normal-loop" d="M 150 70 C 130 18, 250 18, 230 66" fill="none" stroke="#C2EA00" stroke-width="1.8" stroke-dasharray="4,3" marker-end="url(#arrow-volt)"/>

            <!-- 2. NORMAL -> SOSPECHA (a: anomalía L1) -->
            <path id="path-norm-sosp" d="M 300 95 L 492 95" fill="none" stroke="#7E22CE" stroke-width="2" marker-end="url(#arrow-violet)"/>

            <!-- 3. SOSPECHA -> NORMAL (n: falso positivo) -->
            <path id="path-sosp-norm" d="M 500 130 C 440 160, 360 160, 308 130" fill="none" stroke="#C2EA00" stroke-width="1.8" stroke-dasharray="4,3" marker-end="url(#arrow-volt)"/>

            <!-- 4. SOSPECHA -> CONFIRMANDO (a: persiste anomalía) -->
            <path id="path-sosp-conf" d="M 610 145 L 610 332" fill="none" stroke="#7E22CE" stroke-width="2" marker-end="url(#arrow-violet)"/>

            <!-- 5. CONFIRMANDO -> NORMAL (d: no confirmada / descarte) -->
            <path id="path-conf-norm" d="M 500 340 C 450 250, 340 200, 286 148" fill="none" stroke="#C2EA00" stroke-width="1.8" stroke-dasharray="4,3" marker-end="url(#arrow-volt)"/>

            <!-- 6. CONFIRMANDO -> EVENTO (cd: degradación confirmada) -->
            <path id="path-conf-event" d="M 500 365 L 308 365" fill="none" stroke="#7E22CE" stroke-width="2" marker-end="url(#arrow-violet)"/>

            <!-- 7. NORMAL -> EVENTO (co: caída total L0 BYPASS DIRECTO) -->
            <path id="path-bypass-co" d="M 190 145 L 190 332" fill="none" stroke="#F43F5E" stroke-width="3" marker-end="url(#arrow-crimson)"/>

            <!-- 8. EVENTO -> NORMAL (n: recuperación 1/3 -> 2/3 -> 3/3) -->
            <path id="path-recup" d="M 80 380 C 10 290, 10 160, 72 105" fill="none" stroke="#C2EA00" stroke-width="2" stroke-dasharray="5,3" marker-end="url(#arrow-volt)"/>


            <!-- CAPA 2: ETIQUETAS DE TRANSICIÓN (TEXTO FLOTANTE SIN SUPERPOSICIÓN DE LÍNEAS) -->
            <!-- 1. Label: n — normal (5s) (Arriba del bucle) -->
            <text x="190" y="14" fill="#C2EA00" font-family="IBM Plex Mono" font-size="9.5" font-weight="700" text-anchor="middle">n — normal (5s)</text>

            <!-- 2. Label: a — anomalía L1 (Arriba de la línea horizontal superior) -->
            <text x="396" y="83" fill="#7E22CE" font-family="IBM Plex Mono" font-size="9.5" font-weight="700" text-anchor="middle">a — anomalía L1</text>

            <!-- 3. Label: n — falso positivo (Debajo de la curva superior de retorno) -->
            <text x="396" y="172" fill="#C2EA00" font-family="IBM Plex Mono" font-size="9.5" font-weight="700" text-anchor="middle">n — falso positivo</text>

            <!-- 4. Label: a — persiste anomalía (A la derecha de la línea vertical derecha) -->
            <text x="626" y="238" fill="#7E22CE" font-family="IBM Plex Mono" font-size="9.5" font-weight="700" text-anchor="start">a — persiste anomalía</text>

            <!-- 5. Label: d — no confirmada (En espacio libre diagonal) -->
            <text x="360" y="255" fill="#C2EA00" font-family="IBM Plex Mono" font-size="9.5" font-weight="700" text-anchor="middle">d — no confirmada</text>

            <!-- 6. Label: cd — degradación conf. (Arriba de la línea horizontal inferior) -->
            <text x="396" y="352" fill="#7E22CE" font-family="IBM Plex Mono" font-size="9.5" font-weight="700" text-anchor="middle">cd — degradación conf.</text>

            <!-- 7. Label: co — CAÍDA L0 (BYPASS) (A la derecha de la línea vertical izquierda) -->
            <text x="204" y="238" fill="#F43F5E" font-family="IBM Plex Mono" font-size="10" font-weight="800" text-anchor="start">co — CAÍDA L0 (BYPASS)</text>

            <!-- 8. Label: n (recup 1-3/3) (A la izquierda de la curva exterior) -->
            <text x="45" y="238" fill="#C2EA00" font-family="IBM Plex Mono" font-size="9" font-weight="700" text-anchor="end">n (recup 1-3/3)</text>


            <!-- CAPA 3: NODOS CANÓNICOS FSM (TOP LAYER) -->
            <g id="node-NORMAL" class="fsm-node active node-NORMAL">
              <rect x="80" y="70" width="220" height="75" rx="8"/>
              <text x="190" y="104" class="node-title" fill="#C2EA00" font-family="IBM Plex Mono" font-size="13" font-weight="700" text-anchor="middle">● NORMAL</text>
              <text x="190" y="124" class="node-sub" fill="#8F94A0" font-family="IBM Plex Mono" font-size="10" text-anchor="middle">L1: 5 s | Base continua</text>
            </g>

            <g id="node-SOSPECHA" class="fsm-node node-SOSPECHA">
              <rect x="500" y="70" width="220" height="75" rx="8"/>
              <text x="610" y="104" class="node-title" fill="#7E22CE" font-family="IBM Plex Mono" font-size="13" font-weight="700" text-anchor="middle">● SOSPECHA</text>
              <text x="610" y="124" class="node-sub" fill="#8F94A0" font-family="IBM Plex Mono" font-size="10" text-anchor="middle">L1: 1 s | Adaptativo</text>
            </g>

            <g id="node-CONFIRMANDO" class="fsm-node node-CONFIRMANDO">
              <rect x="500" y="340" width="220" height="75" rx="8"/>
              <text x="610" y="374" class="node-title" fill="#7E22CE" font-family="IBM Plex Mono" font-size="13" font-weight="700" text-anchor="middle">● CONFIRMANDO</text>
              <text x="610" y="394" class="node-sub" fill="#8F94A0" font-family="IBM Plex Mono" font-size="10" text-anchor="middle">Fast &rarr; Ookla | Evidencia</text>
            </g>

            <g id="node-EVENTO" class="fsm-node node-EVENTO">
              <rect x="80" y="340" width="220" height="75" rx="8"/>
              <text x="190" y="374" class="node-title" id="node-EVENTO-title" fill="#F43F5E" font-family="IBM Plex Mono" font-size="13" font-weight="700" text-anchor="middle">● EVENTO</text>
              <text x="190" y="394" class="node-sub" id="node-EVENTO-sub" fill="#8F94A0" font-family="IBM Plex Mono" font-size="10" text-anchor="middle">cd (degradación) / co (caída)</text>
            </g>
          </svg>
        </div>
      </div>

      <div class="grid-2">
        <div class="card">
          <div class="card-label">Detalles del Estado Actual</div>
          <div style="font-family:var(--font-mono); font-size:12px; margin-top:10px; display:flex; flex-direction:column; gap:8px;">
            <div>Estado canónico: <span id="fsm-detail-state" class="badge-tag tag-normal">NORMAL</span></div>
            <div>Frecuencia L1 adaptativa: <span id="fsm-detail-freq">5 segundos</span></div>
            <div>Contador de recuperación: <span id="fsm-detail-rec">0 / 3</span></div>
            <div>Tiempo transcurrido: <span id="fsm-detail-time">00:00:00</span></div>
            <div>Último símbolo recibido: <span id="fsm-detail-sym">n (lectura normal)</span></div>
            <div id="fsm-detail-event-info" style="display:none; color:var(--brand-accent); padding-top:4px; border-top:1px solid var(--border-color);">--</div>
          </div>
        </div>
        <div class="card">
          <div class="card-label">Matriz de Símbolos & Reglas Mealy V2</div>
          <table class="terminal-table" style="margin-top:6px;">
            <thead>
              <tr>
                <th>SÍMBOLO</th>
                <th>TIPO</th>
                <th>TRANSICIÓN / ACCIÓN</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><span class="badge-tag tag-normal">● n</span></td>
                <td>Normal</td>
                <td>Operación continua L1 a 5s / Recuperación</td>
              </tr>
              <tr>
                <td><span class="badge-tag tag-degraded">● a</span></td>
                <td>Anomalía L1</td>
                <td>NORMAL &rarr; SOSPECHA (Muestreo a 1s)</td>
              </tr>
              <tr>
                <td><span class="badge-tag tag-degraded">● cd</span></td>
                <td>Degradación</td>
                <td>CONFIRMANDO &rarr; EVENTO (Evidencia confirmada)</td>
              </tr>
              <tr>
                <td><span class="badge-tag tag-outage">● co</span></td>
                <td>Caída total L0</td>
                <td>NORMAL &rarr; EVENTO (BYPASS DIRECTO)</td>
              </tr>
              <tr>
                <td><span class="badge-tag tag-normal">● d</span></td>
                <td>Descarte</td>
                <td>CONFIRMANDO &rarr; NORMAL (Falso positivo desestimado)</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Vista 3 — Sensores -->
    <div id="view-sensors" class="view-container">
      <div class="grid-2">
        <div class="card">
          <div class="card-label">
            <span>CONECTIVIDAD BASE</span>
            <span class="sensor-badge"><span class="sensor-dot-l0">◉</span> L0</span>
          </div>
          <div style="font-family:var(--font-mono); font-size:12px; margin-top:10px; display:flex; flex-direction:column; gap:6px;">
            <div>Gateway: <span id="sen-l0-gw">[ OK ]</span></div>
            <div>DNS Resolution: <span id="sen-l0-dns">[ OK ]</span></div>
            <div>TCP Connection: <span id="sen-l0-tcp">[ OK ]</span></div>
            <div>HTTPS Reachability: <span id="sen-l0-http">[ OK ]</span></div>
            <div style="margin-top:8px; color:var(--text-faint);" id="sen-l0-ts">Última ejecución: --:--:--</div>
          </div>
        </div>

        <div class="card">
          <div class="card-label">
            <span>MICRO-THROUGHPUT</span>
            <span class="sensor-badge"><span class="sensor-dot-l1">◉</span> L1</span>
          </div>
          <div style="font-family:var(--font-mono); font-size:12px; margin-top:10px; display:flex; flex-direction:column; gap:6px;">
            <div>Throughput: <span id="sen-l1-tp" style="color:var(--text-important); font-weight:700;">-- Mbps</span></div>
            <div>Intervalo activo: <span id="sen-l1-int">5 s (NORMAL)</span></div>
            <div>Streams utilizados: <span id="sen-l1-str">4</span></div>
            <div>Tiempo medición: <span id="sen-l1-ms">-- ms</span></div>
            <div style="margin-top:8px; color:var(--text-faint);" id="sen-l1-ts">Última ejecución: --:--:--</div>
          </div>
        </div>
      </div>

      <div class="grid-2">
        <div class="card">
          <div class="card-label">
            <span>FAST.COM (CONFIRMACIÓN INTERMEDIA)</span>
            <span class="sensor-badge"><span class="sensor-dot-fast">◉</span> FAST</span>
          </div>
          <div style="font-family:var(--font-mono); font-size:12px; margin-top:10px; display:flex; flex-direction:column; gap:6px;">
            <div>Estado de ejecución: <span id="sen-fast-exec" class="badge-tag tag-brand">NO EJECUTADO</span></div>
            <div>Motivo: <span id="sen-fast-reason" style="color:var(--text-muted);">No necesario en estado NORMAL</span></div>
            <div>Última medición: <span id="sen-fast-val">-- Mbps</span></div>
          </div>
        </div>

        <div class="card">
          <div class="card-label">
            <span>OOKLA SPEEDTEST (CONFIRMACIÓN FINAL)</span>
            <span class="sensor-badge"><span class="sensor-dot-ookla">◉</span> OOKLA</span>
          </div>
          <div style="font-family:var(--font-mono); font-size:12px; margin-top:10px; display:flex; flex-direction:column; gap:6px;">
            <div>Estado de ejecución: <span id="sen-ookla-exec" class="badge-tag tag-brand">NO EJECUTADO</span></div>
            <div>Motivo: <span id="sen-ookla-reason" style="color:var(--text-muted);">No necesario sin confirmación de Fast.com</span></div>
            <div>Última medición: <span id="sen-ookla-val">-- Mbps</span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Vista 4 — Eventos -->
    <div id="view-events" class="view-container">
      <div class="filter-bar">
        <button class="filter-btn active" onclick="filterEvents('all', this)">[ Todos ]</button>
        <button class="filter-btn" onclick="filterEvents('degradation', this)">[ Degradación ]</button>
        <button class="filter-btn" onclick="filterEvents('outage', this)">[ Caída total ]</button>
        <button class="filter-btn" onclick="filterEvents('active', this)">[ Activos ]</button>
      </div>

      <div id="events-list-container">
        <div class="card" style="color:var(--text-muted); font-family:var(--font-mono); font-size:12px;">Cargando registro de incidentes...</div>
      </div>
    </div>

    <!-- Vista 5 — Timeline -->
    <div id="view-timeline" class="view-container">
      <div class="card">
        <div class="card-label" style="margin-bottom:16px;">Línea de Tiempo Sincronizada (Mediciones Mbps + FSM + Evidencias)</div>
        <div class="chart-container">
          <canvas id="timelineChart"></canvas>
        </div>
      </div>
    </div>

    <!-- Vista 6 — Reportes Técnicos V2 -->
    <div id="view-reports" class="view-container">
      <div class="card" style="margin-bottom:16px;">
        <div class="card-label" style="font-size:14px; font-weight:bold; color:var(--volt-accent); margin-bottom:12px;">
          TRACEFLY V2 — GENERADOR DE REPORTES TÉCNICOS AUDITABLES (EVIDENCIA ISP)
        </div>
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin-bottom:16px;">
          <div>
            <label style="display:block; font-size:11px; color:#8F94A0; margin-bottom:4px;">Fecha/Hora Inicio (Desde):</label>
            <input type="datetime-local" id="rep-desde" style="width:100%; background:#0F1115; border:1px solid #2B2D33; color:#FFF; padding:8px; border-radius:4px; font-family:'IBM Plex Mono', monospace; font-size:12px;">
          </div>
          <div>
            <label style="display:block; font-size:11px; color:#8F94A0; margin-bottom:4px;">Fecha/Hora Fin (Hasta):</label>
            <input type="datetime-local" id="rep-hasta" style="width:100%; background:#0F1115; border:1px solid #2B2D33; color:#FFF; padding:8px; border-radius:4px; font-family:'IBM Plex Mono', monospace; font-size:12px;">
          </div>
          <div>
            <label style="display:block; font-size:11px; color:#8F94A0; margin-bottom:4px;">Zona Horaria:</label>
            <select id="rep-tz" style="width:100%; background:#0F1115; border:1px solid #2B2D33; color:#FFF; padding:8px; border-radius:4px; font-family:'IBM Plex Mono', monospace; font-size:12px;">
              <option value="America/Lima" selected>America/Lima (UTC-5)</option>
              <option value="UTC">UTC (Coordinated Universal Time)</option>
              <option value="America/Bogota">America/Bogota</option>
              <option value="America/Santiago">America/Santiago</option>
            </select>
          </div>
        </div>

        <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:16px;">
          <span style="font-size:11px; color:#8F94A0;">Rangos Rápidos:</span>
          <button class="filter-btn" onclick="setReportQuickRange('today')">[ HOY ]</button>
          <button class="filter-btn" onclick="setReportQuickRange('24h')">[ ÚLTIMAS 24H ]</button>
          <button class="filter-btn" onclick="setReportQuickRange('7d')">[ ÚLTIMOS 7 DÍAS ]</button>
          <button class="filter-btn" onclick="setReportQuickRange('month')">[ ESTE MES ]</button>
        </div>

        <div style="display:flex; flex-wrap:wrap; gap:12px; margin-top:12px; border-top:1px solid #2B2D33; padding-top:16px;">
          <button class="filter-btn" style="border-color:var(--volt-accent); color:var(--volt-accent);" onclick="generateReportPreview()">[ GENERAR REPORTE ]</button>
          <button class="filter-btn" style="background:var(--volt-accent); color:#000; font-weight:bold;" onclick="downloadReportPdf()">[ DESCARGAR PDF ]</button>
          <button class="filter-btn" style="border-color:#8F94A0; color:#8F94A0;" onclick="downloadReportSha256()">[ DESCARGAR .SHA256 ]</button>
        </div>
      </div>

      <div class="grid-4" style="margin-bottom:16px;">
        <div class="card">
          <div class="card-label">Disponibilidad Conectividad (L0)</div>
          <div class="card-value" id="rep-uptime" style="color:var(--volt-accent);">100.0%</div>
        </div>
        <div class="card">
          <div class="card-label">Degradaciones Confirmadas (cd)</div>
          <div class="card-value" id="rep-deg-count">0</div>
        </div>
        <div class="card">
          <div class="card-label">Caídas Totales (co)</div>
          <div class="card-value" id="rep-out-count">0</div>
        </div>
        <div class="card">
          <div class="card-label">Tiempo Afectado Total</div>
          <div class="card-value" id="rep-affected-time">0 s</div>
        </div>
      </div>

      <div class="card" id="rep-preview-card" style="display:none;">
        <div class="card-label" style="margin-bottom:8px; color:var(--volt-accent);">PREVISUALIZACIÓN TÉCNICA DEL REPORTE V2</div>
        <div id="rep-preview-content" style="font-family:'IBM Plex Mono', monospace; font-size:12px; color:#D1D5DB; line-height:1.6; white-space:pre-wrap; background:#0F1115; padding:12px; border-radius:4px; border:1px solid #2B2D33;"></div>
      </div>
    </div>
  </main>
</div>

<script>
  let currentView = 'dashboard';
  let timelineChartObj = null;

  function formatLocalTime(isoStr) {
    if (!isoStr) return '--:--:--';
    try {
      let clean = String(isoStr).replace(' ', 'T');
      if (!clean.includes('Z') && !clean.includes('+') && !clean.includes('-', 10)) {
        clean += 'Z';
      }
      const d = new Date(clean);
      if (isNaN(d.getTime())) return isoStr;
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    } catch (e) {
      return isoStr;
    }
  }

  function formatLocalDateTime(isoStr) {
    if (!isoStr) return '--:--:--';
    try {
      let clean = String(isoStr).replace(' ', 'T');
      if (!clean.includes('Z') && !clean.includes('+') && !clean.includes('-', 10)) {
        clean += 'Z';
      }
      const d = new Date(clean);
      if (isNaN(d.getTime())) return isoStr;
      return d.toLocaleString([], { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    } catch (e) {
      return isoStr;
    }
  }

  function switchView(viewName, element) {
    currentView = viewName;
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.view-container').forEach(el => el.classList.remove('active'));

    element.classList.add('active');
    document.getElementById('view-' + viewName).classList.add('active');

    const titles = {
      'dashboard': 'DASHBOARD OPERATIVO',
      'fsm': 'VISUALIZADOR MEALY FSM',
      'sensors': 'OBSERVACIÓN Y SENSORES',
      'events': 'REGISTRO DE INCIDENTES',
      'timeline': 'LÍNEA DE TIEMPO SINCRONIZADA',
      'reports': 'REPORTES Y EXPORTACIÓN'
    };
    document.getElementById('view-title').textContent = titles[viewName] || 'TRACEFLY V2';

    if (viewName === 'timeline') {
      renderTimelineChart();
    }
  }

  async function fetchStatus() {
    try {
      const res = await fetch('/api/v2/status');
      const data = await res.json();
      
      const now = new Date();
      document.getElementById('sync-time').textContent = 'SYNC: ' + now.toLocaleTimeString([], { hour12: false });

      // Banner & State
      const state = data.current_state || 'NORMAL';
      const banner = document.getElementById('fsm-banner');
      banner.className = 'fsm-banner state-' + state;
      document.getElementById('banner-state-title').textContent = 'ESTADO: ' + state;
      document.getElementById('dash-fsm-val').textContent = state;
      
      const interval = data.l1_adaptive_interval_s || 5;
      document.getElementById('dash-l1-sub').textContent = 'Intervalo actual: ' + interval + ' s';

      // Readings
      const l0 = data.latest_readings.l0;
      if (l0) {
        document.getElementById('dash-l0-val').textContent = (l0.latency_ms ? l0.latency_ms.toFixed(1) : '0') + ' ms';
      }

      const l1 = data.latest_readings.l1;
      if (l1) {
        document.getElementById('dash-l1-val').textContent = l1.throughput_mbps.toFixed(1) + ' Mbps';
        document.getElementById('banner-last-ts').textContent = formatLocalTime(l1.timestamp);
      }

      // FSM Nodes highlight & Event details
      ['NORMAL', 'SOSPECHA', 'CONFIRMANDO', 'EVENTO'].forEach(s => {
        const node = document.getElementById('node-' + s);
        if (node) {
          if (s === state) node.classList.add('active');
          else node.classList.remove('active');
        }
      });

      const detailStateEl = document.getElementById('fsm-detail-state');
      detailStateEl.textContent = state;
      if (state === 'NORMAL') {
        detailStateEl.className = 'badge-tag tag-normal';
      } else if (state === 'SOSPECHA' || state === 'CONFIRMANDO' || state.includes('DEG')) {
        detailStateEl.className = 'badge-tag tag-degraded';
      } else {
        detailStateEl.className = 'badge-tag tag-outage';
      }

      // Información dinámica cuando el estado actual sea EVENTO
      const nodeEventTitle = document.getElementById('node-EVENTO-title');
      const nodeEventSub = document.getElementById('node-EVENTO-sub');
      const detailEventInfo = document.getElementById('fsm-detail-event-info');
      const recCounter = (data.recovery_counter !== undefined) ? data.recovery_counter : 0;
      const recK = data.recovery_k || 3;
      document.getElementById('fsm-detail-rec').textContent = recCounter + ' / ' + recK;

      // Reset de resaltado estático en rutas SVG
      ['normal-loop', 'norm-sosp', 'sosp-norm', 'sosp-conf', 'conf-norm', 'conf-event', 'bypass-co', 'recup'].forEach(p => {
        const pathEl = document.getElementById('path-' + p);
        if (pathEl) pathEl.classList.remove('fsm-path-active');
      });

      // Resaltado estático según estado e input_symbol
      if (state === 'NORMAL') {
        const pLoop = document.getElementById('path-normal-loop');
        if (pLoop) pLoop.classList.add('fsm-path-active');
        if (recCounter > 0) {
          const pRec = document.getElementById('path-recup');
          if (pRec) pRec.classList.add('fsm-path-active');
        }
      } else if (state === 'SOSPECHA') {
        const pSosp = document.getElementById('path-norm-sosp');
        if (pSosp) pSosp.classList.add('fsm-path-active');
      } else if (state === 'CONFIRMANDO') {
        const pConf = document.getElementById('path-sosp-conf');
        if (pConf) pConf.classList.add('fsm-path-active');
      } else if (state === 'EVENTO' || state.includes('CAIDA') || state.includes('DEG')) {
        if (data.input_symbol === 'co' || (data.active_event && data.active_event.event_type && data.active_event.event_type.includes('CAIDA'))) {
          const pBypass = document.getElementById('path-bypass-co');
          if (pBypass) pBypass.classList.add('fsm-path-active');
        } else {
          const pEvent = document.getElementById('path-conf-event');
          if (pEvent) pEvent.classList.add('fsm-path-active');
        }
        if (recCounter > 0) {
          const pRec = document.getElementById('path-recup');
          if (pRec) pRec.classList.add('fsm-path-active');
        }
      }

      if (state === 'EVENTO' || state.includes('CAIDA') || state.includes('DEG')) {
        const evType = (data.active_event && data.active_event.event_type) || (data.input_symbol === 'co' ? 'CAÍDA TOTAL L0' : 'DEGRADACIÓN CONFIRMADA');
        if (nodeEventTitle) nodeEventTitle.textContent = '● EVENTO (' + (data.input_symbol || 'ACTIVO') + ')';
        if (nodeEventSub) nodeEventSub.textContent = evType + ' | Recup: ' + recCounter + '/' + recK;
        if (detailEventInfo) {
          detailEventInfo.style.display = 'block';
          detailEventInfo.innerHTML = 'Evento activo: <b>' + evType + '</b> | Progreso recuperación: <b>' + recCounter + '/' + recK + '</b>';
        }
      } else {
        if (nodeEventTitle) nodeEventTitle.textContent = '● EVENTO';
        if (nodeEventSub) nodeEventSub.textContent = 'cd (degradación) / co (caída)';
        if (detailEventInfo) detailEventInfo.style.display = 'none';
      }
    } catch (e) {
      console.error('Error fetching status:', e);
    }
  }

  async function fetchActivity() {
    try {
      const res = await fetch('/api/v2/fsm-history?limit=15');
      const history = await res.json();
      const tbody = document.getElementById('dash-activity-body');
      tbody.innerHTML = '';

      if (!history || history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted);">Sin transiciones registradas</td></tr>';
        return;
      }

      history.forEach(row => {
        const tr = document.createElement('tr');
        const time = formatLocalTime(row.timestamp);
        
        let tagClass = 'tag-brand';
        if (row.input_symbol === 'n') tagClass = 'tag-normal';
        else if (row.input_symbol === 'a' || row.input_symbol === 'cd') tagClass = 'tag-degraded';
        else if (row.input_symbol === 'co') tagClass = 'tag-outage';

        const symbolTag = `<span class="badge-tag ${tagClass}">${row.input_symbol}</span>`;
        
        tr.innerHTML = `
          <td>${time}</td>
          <td>FSM</td>
          <td>${symbolTag}</td>
          <td>${row.current_state} &rarr; ${row.next_state}</td>
          <td>${row.output_action || 'TRANSICIÓN'}</td>
        `;
        tbody.appendChild(tr);
      });
    } catch (e) {
      console.error('Error fetching activity:', e);
    }
  }

  async function fetchSensors() {
    try {
      const res = await fetch('/api/v2/sensors-status');
      const data = await res.json();

      if (data.l0 && data.l0.last_reading) {
        document.getElementById('sen-l0-ts').textContent = 'Última ejecución: ' + formatLocalTime(data.l0.last_reading.timestamp);
      }

      if (data.l1 && data.l1.last_reading) {
        document.getElementById('sen-l1-tp').textContent = data.l1.last_reading.throughput_mbps.toFixed(1) + ' Mbps';
        document.getElementById('sen-l1-int').textContent = data.l1.interval_seconds + ' s';
        document.getElementById('sen-l1-ts').textContent = 'Última ejecución: ' + formatLocalTime(data.l1.last_reading.timestamp);
      }

      const fast = data.fast;
      document.getElementById('sen-fast-exec').textContent = fast.is_executed ? 'EJECUTADO' : 'NO EJECUTADO';
      document.getElementById('sen-fast-reason').textContent = fast.reason;
      if (fast.last_reading) {
        document.getElementById('sen-fast-val').textContent = fast.last_reading.throughput_mbps.toFixed(1) + ' Mbps';
      }

      const ookla = data.ookla;
      document.getElementById('sen-ookla-exec').textContent = ookla.is_executed ? 'EJECUTADO' : 'NO EJECUTADO';
      document.getElementById('sen-ookla-reason').textContent = ookla.reason;
      if (ookla.last_reading) {
        document.getElementById('sen-ookla-val').textContent = ookla.last_reading.download_mbps.toFixed(1) + ' Mbps';
      }
    } catch (e) {
      console.error('Error fetching sensors:', e);
    }
  }

  let allEvents = [];
  async function fetchEvents() {
    try {
      const res = await fetch('/api/v2/events');
      allEvents = await res.json();
      renderEvents(allEvents);
    } catch (e) {
      console.error('Error fetching events:', e);
    }
  }

  function renderEvents(events) {
    const container = document.getElementById('events-list-container');
    container.innerHTML = '';

    if (!events || events.length === 0) {
      container.innerHTML = '<div class="card" style="color:var(--text-muted); font-family:var(--font-mono); font-size:12px;">Sin incidentes registrados.</div>';
      return;
    }

    events.forEach(ev => {
      const card = document.createElement('div');
      card.className = 'incident-card ' + (ev.end_time ? 'recovered' : '');

      const start = formatLocalDateTime(ev.start_time);
      const end = ev.end_time ? formatLocalDateTime(ev.end_time) : 'EN CURSO';
      const dur = ev.duration_seconds ? ev.duration_seconds + ' s' : 'Observando';

      card.innerHTML = `
        <div class="incident-header">
          <div class="incident-id">#${String(ev.id).padStart(3, '0')} ${ev.event_type || 'INCIDENTE'}</div>
          <span class="badge-tag ${ev.end_time ? 'tag-normal' : 'tag-outage'}">${ev.end_time ? 'RECUPERADO' : 'ACTIVO'}</span>
        </div>
        <div class="incident-grid">
          <div>
            <div class="incident-prop-label">Inicio observado</div>
            <div>${start}</div>
          </div>
          <div>
            <div class="incident-prop-label">Confirmado</div>
            <div>${start}</div>
          </div>
          <div>
            <div class="incident-prop-label">Recuperado</div>
            <div>${end}</div>
          </div>
          <div>
            <div class="incident-prop-label">Duración observada</div>
            <div>${dur}</div>
          </div>
        </div>
      `;
      container.appendChild(card);
    });
  }

  function filterEvents(type, btn) {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    if (type === 'all') renderEvents(allEvents);
    else if (type === 'active') renderEvents(allEvents.filter(e => e.is_active));
    else if (type === 'degradation') renderEvents(allEvents.filter(e => (e.event_type || '').includes('DEGRADACIÓN') || (e.event_type || '').includes('DEGRADACION')));
    else if (type === 'outage') renderEvents(allEvents.filter(e => (e.event_type || '').includes('CAÍDA') || (e.event_type || '').includes('CAIDA')));
  }

  async function renderTimelineChart() {
    try {
      const res = await fetch('/api/v2/timeline?limit=100');
      const data = await res.json();

      const ctx = document.getElementById('timelineChart').getContext('2d');
      if (timelineChartObj) timelineChartObj.destroy();

      const labels = (data.l1 || []).map(r => formatLocalTime(r.timestamp));
      const l1Vals = (data.l1 || []).map(r => r.throughput_mbps);

      timelineChartObj = new Chart(ctx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: 'L1 Throughput (Mbps)',
            data: l1Vals,
            borderColor: '#CFF021',
            backgroundColor: 'rgba(207, 240, 33, 0.08)',
            borderWidth: 2,
            tension: 0.2,
            fill: true
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { ticks: { color: '#9E9EA5', font: { family: 'IBM Plex Mono', size: 10 } }, grid: { color: '#2A2B30' } },
            y: { ticks: { color: '#9E9EA5', font: { family: 'IBM Plex Mono', size: 10 } }, grid: { color: '#2A2B30' } }
          },
          plugins: {
            legend: { labels: { color: '#FFFFFF', font: { family: 'IBM Plex Mono', size: 11 } } }
          }
        }
      });
    } catch (e) {
      console.error('Error rendering timeline:', e);
    }
  }

  function exportData(format) {
    window.open('/api/v2/events?limit=500', '_blank');
  }

  // Reportes Técnicos V2 JS Handlers
  function initReportDateDefaults() {
    const now = new Date();
    const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0);
    
    const tzOffset = now.getTimezoneOffset() * 60000;
    const localStart = new Date(startOfDay.getTime() - tzOffset).toISOString().slice(0, 16);
    const localNow = new Date(now.getTime() - tzOffset).toISOString().slice(0, 16);
    
    const elStart = document.getElementById('rep-desde');
    const elEnd = document.getElementById('rep-hasta');
    if (elStart && !elStart.value) elStart.value = localStart;
    if (elEnd && !elEnd.value) elEnd.value = localNow;
  }

  function setReportQuickRange(range) {
    const now = new Date();
    let start = new Date();
    
    if (range === 'today') {
      start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0);
    } else if (range === '24h') {
      start = new Date(now.getTime() - 24 * 3600 * 1000);
    } else if (range === '7d') {
      start = new Date(now.getTime() - 7 * 24 * 3600 * 1000);
    } else if (range === 'month') {
      start = new Date(now.getFullYear(), now.getMonth(), 1, 0, 0, 0);
    }
    
    const tzOffset = now.getTimezoneOffset() * 60000;
    document.getElementById('rep-desde').value = new Date(start.getTime() - tzOffset).toISOString().slice(0, 16);
    document.getElementById('rep-hasta').value = new Date(now.getTime() - tzOffset).toISOString().slice(0, 16);
  }

  function getReportQueryParams() {
    let desde = document.getElementById('rep-desde').value;
    let hasta = document.getElementById('rep-hasta').value;
    const tz = document.getElementById('rep-tz').value || 'America/Lima';

    if (desde) desde = desde.replace('T', ' ') + ':00';
    if (hasta) hasta = hasta.replace('T', ' ') + ':59';

    if (!desde) desde = '2020-01-01 00:00:00';
    if (!hasta) hasta = '2099-12-31 23:59:59';

    return `desde=${encodeURIComponent(desde)}&hasta=${encodeURIComponent(hasta)}&tz=${encodeURIComponent(tz)}`;
  }

  async function generateReportPreview() {
    const params = getReportQueryParams();
    const card = document.getElementById('rep-preview-card');
    const content = document.getElementById('rep-preview-content');

    content.textContent = "Generando previsualización técnica del reporte...";
    card.style.display = 'block';

    try {
      const res = await fetch(`/api/v2/reporte/preview?${params}`);
      const data = await res.json();

      document.getElementById('rep-uptime').textContent = data.kpis ? data.kpis.disponibilidad_pct : '100.0%';
      document.getElementById('rep-deg-count').textContent = data.kpis ? data.kpis.degradaciones : '0';
      document.getElementById('rep-out-count').textContent = data.kpis ? data.kpis.caidas : '0';
      document.getElementById('rep-affected-time').textContent = data.kpis ? data.kpis.tiempo_afectado_str : '0 s';

      let text = `========================================================================\n`;
      text += `TRACEFLY V2 — PREVISUALIZACIÓN DE REPORTE TÉCNICO AUDITABLE\n`;
      text += `========================================================================\n\n`;
      text += `Identificador del Reporte: ${data.report_id}\n`;
      text += `Agente Emisor:            ${data.agent_id}\n`;
      text += `Período Analizado:         ${data.desde}  —  ${data.hasta}\n`;
      text += `Zona Horaria:             ${data.tz}\n\n`;

      if (data.version_warning) {
        text += `[!] ${data.version_warning}\n\n`;
      }

      text += `--- RESUMEN EJECUTIVO ---\n`;
      text += `• Tiempo Monitoreado:       ${data.periodo_monitoreado_str}\n`;
      text += `• Total Eventos:            ${data.kpis.total_eventos}\n`;
      text += `• Degradaciones (cd):        ${data.kpis.degradaciones}\n`;
      text += `• Caídas Totales (co):      ${data.kpis.caidas}\n`;
      text += `• Disponibilidad Conectividad: ${data.kpis.disponibilidad_pct}\n`;
      text += `• Tiempo en Degradación:     ${data.kpis.tiempo_degradado_str}\n`;
      text += `• Tiempo en Caída Total:    ${data.kpis.tiempo_caida_str}\n`;
      text += `• Tiempo Afectado Total:    ${data.kpis.tiempo_afectado_str}\n\n`;

      text += `--- REGISTRO DE INCIDENTES (${data.incidentes.length}) ---\n`;
      if (data.incidentes.length === 0) {
        text += `Sin incidentes registrados en el período.\n`;
      } else {
        data.incidentes.forEach((inc, idx) => {
          text += `\n[${idx + 1}] ${inc.id} | ${inc.init_type}\n`;
          text += `    Evolución:   ${inc.evolution}\n`;
          text += `    Inicio:      ${inc.start_time}\n`;
          text += `    Duración:    ${inc.duracion_total_str} (Degradado: ${inc.duracion_deg_str}, Caída: ${inc.duracion_caida_str})\n`;
          text += `    Sensores:    L0:${inc.sensors.l0 ? '✓' : '—'} L1:${inc.sensors.l1 ? '✓' : '—'} Fast:${inc.sensors.fast ? '✓' : 'No ejec.'} Ookla:${inc.sensors.ookla ? '✓' : 'No ejec.'}\n`;
          if (inc.sensor_bypass_note) {
            text += `    Nota Bypass: ${inc.sensor_bypass_note}\n`;
          }
        });
      }

      text += `\n--- ESTADO DE INTEGRIDAD ---\n`;
      text += `Firma SHA-256: Disponible al descargar el paquete PDF + .sha256\n`;

      content.textContent = text;
    } catch (e) {
      content.textContent = "Error al obtener la previsualización del reporte: " + e.message;
    }
  }

  function downloadReportPdf() {
    const params = getReportQueryParams();
    window.open(`/api/v2/reporte/pdf?${params}`, '_blank');
  }

  function downloadReportSha256() {
    const params = getReportQueryParams();
    window.open(`/api/v2/reporte/sha256?${params}`, '_blank');
  }

  // Periodic polling
  setInterval(() => {
    fetchStatus();
    fetchActivity();
    fetchSensors();
    if (currentView === 'events') fetchEvents();
  }, 3000);

  // Initial load
  initReportDateDefaults();
  fetchStatus();
  fetchActivity();
  fetchSensors();
  fetchEvents();
</script>
</body>
</html>
"""
