"""Router de estado — GET / (dashboard HTML) y GET /api/status (JSON)."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

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
    """Dashboard visual completo ControlInternet V2 (Ultraminimalista, 6 Vistas, 0 Emojis)."""
    return DASHBOARD_HTML


# ---------------------------------------------------------------------------
# Dashboard HTML SPA Ultraminimalista V2 (0 Emojis)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CONTROLINTERNET V2 — Observacion & FSM Mealy</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg-dark: #0B0E14;
    --panel-bg: #131720;
    --panel-border: #1F2633;
    --text-primary: #E6EDF3;
    --text-muted: #8B949E;
    --text-faint: #484F58;
    --color-normal: #2EA043;
    --color-normal-bg: rgba(46, 160, 67, 0.12);
    --color-sospecha: #D29922;
    --color-sospecha-bg: rgba(210, 153, 34, 0.12);
    --color-confirmando: #DB6D28;
    --color-confirmando-bg: rgba(219, 109, 40, 0.12);
    --color-evento: #F85149;
    --color-evento-bg: rgba(248, 81, 73, 0.12);
    --color-blue: #58A6FF;
    --font-mono: 'IBM Plex Mono', monospace;
    --font-sans: 'Public Sans', sans-serif;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg-dark);
    color: var(--text-primary);
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
    background: var(--panel-bg);
    border-right: 1px solid var(--panel-border);
    display: flex;
    flex-direction: column;
    padding: 20px 16px;
  }

  .sidebar-header {
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--panel-border);
  }

  .brand-title {
    font-family: var(--font-mono);
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--text-primary);
  }

  .brand-sub {
    font-size: 10px;
    font-weight: 600;
    color: var(--text-muted);
    letter-spacing: 0.03em;
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
    color: var(--text-muted);
    cursor: pointer;
    transition: all 0.15s ease;
    border: 1px solid transparent;
    text-decoration: none;
  }

  .nav-item:hover {
    color: var(--text-primary);
    background: rgba(255, 255, 255, 0.03);
  }

  .nav-item.active {
    color: var(--text-primary);
    background: var(--panel-border);
    border-color: rgba(255, 255, 255, 0.08);
  }

  .nav-num {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--text-faint);
  }

  .sidebar-footer {
    padding-top: 16px;
    border-top: 1px solid var(--panel-border);
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--text-faint);
  }

  /* Content Area */
  .content-area {
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow-y: auto;
    background: var(--bg-dark);
  }

  .topbar {
    height: 56px;
    border-bottom: 1px solid var(--panel-border);
    padding: 0 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--panel-bg);
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
    border: 1px solid var(--panel-border);
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
    background: var(--panel-bg);
    border: 1px solid var(--panel-border);
    border-radius: 6px;
    padding: 16px;
    margin-bottom: 16px;
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
  }

  .card-value {
    font-family: var(--font-mono);
    font-size: 20px;
    font-weight: 700;
    color: var(--text-primary);
  }

  .card-sub {
    font-size: 11px;
    color: var(--text-muted);
    margin-top: 4px;
  }

  /* FSM Banner */
  .fsm-banner {
    padding: 20px;
    border-radius: 6px;
    border: 1px solid var(--panel-border);
    margin-bottom: 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .fsm-banner.state-NORMAL {
    background: var(--color-normal-bg);
    border-color: rgba(46, 160, 67, 0.3);
  }

  .fsm-banner.state-SOSPECHA {
    background: var(--color-sospecha-bg);
    border-color: rgba(210, 153, 34, 0.3);
  }

  .fsm-banner.state-CONFIRMANDO {
    background: var(--color-confirmando-bg);
    border-color: rgba(219, 109, 40, 0.3);
  }

  .fsm-banner.state-EVENTO {
    background: var(--color-evento-bg);
    border-color: rgba(248, 81, 73, 0.3);
  }

  .fsm-state-title {
    font-family: var(--font-mono);
    font-size: 24px;
    font-weight: 700;
    letter-spacing: 0.02em;
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
    color: var(--text-primary);
    margin-top: 2px;
  }

  /* Data Table / Log Terminal Style */
  .terminal-table {
    width: 100%;
    border-collapse: collapse;
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .terminal-table th {
    text-align: left;
    padding: 8px 12px;
    background: rgba(255, 255, 255, 0.02);
    border-bottom: 1px solid var(--panel-border);
    color: var(--text-muted);
    font-weight: 600;
  }

  .terminal-table td {
    padding: 8px 12px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.03);
    color: var(--text-primary);
  }

  .terminal-table tr:hover {
    background: rgba(255, 255, 255, 0.02);
  }

  .badge-tag {
    display: inline-block;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
  }

  .tag-normal { background: var(--color-normal-bg); color: var(--color-normal); }
  .tag-sospecha { background: var(--color-sospecha-bg); color: var(--color-sospecha); }
  .tag-confirmando { background: var(--color-confirmando-bg); color: var(--color-confirmando); }
  .tag-evento { background: var(--color-evento-bg); color: var(--color-evento); }
  .tag-blue { background: rgba(88, 166, 255, 0.12); color: var(--color-blue); }

  /* FSM Diagram Visualizer */
  .fsm-diagram {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 40px 20px;
    position: relative;
  }

  .fsm-node {
    width: 140px;
    height: 100px;
    border: 1px solid var(--panel-border);
    border-radius: 6px;
    background: var(--panel-bg);
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    position: relative;
    z-index: 2;
    transition: all 0.2s ease;
  }

  .fsm-node.active {
    border-width: 2px;
    box-shadow: 0 0 15px rgba(255, 255, 255, 0.08);
  }

  .fsm-node.active.node-NORMAL { border-color: var(--color-normal); }
  .fsm-node.active.node-SOSPECHA { border-color: var(--color-sospecha); }
  .fsm-node.active.node-CONFIRMANDO { border-color: var(--color-confirmando); }
  .fsm-node.active.node-EVENTO { border-color: var(--color-evento); }

  .node-title {
    font-family: var(--font-mono);
    font-size: 13px;
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
    background: var(--panel-border);
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
    border: 1px solid var(--panel-border);
    border-left: 4px solid var(--color-evento);
    background: var(--panel-bg);
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
    background: var(--panel-bg);
    border: 1px solid var(--panel-border);
    color: var(--text-muted);
    padding: 6px 12px;
    border-radius: 4px;
    font-family: var(--font-mono);
    font-size: 11px;
    cursor: pointer;
  }

  .filter-btn.active {
    color: var(--text-primary);
    border-color: var(--text-muted);
    background: rgba(255, 255, 255, 0.05);
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
      <div class="brand-title">CONTROLINTERNET</div>
      <div class="brand-sub">ARQUITECTURA V2</div>
    </div>
    
    <nav class="nav-menu">
      <a class="nav-item active" onclick="switchView('dashboard', this)">
        <span class="nav-num">01</span> Dashboard
      </a>
      <a class="nav-item" onclick="switchView('fsm', this)">
        <span class="nav-num">02</span> Estado FSM
      </a>
      <a class="nav-item" onclick="switchView('sensors', this)">
        <span class="nav-num">03</span> Sensores
      </a>
      <a class="nav-item" onclick="switchView('events', this)">
        <span class="nav-num">04</span> Eventos
      </a>
      <a class="nav-item" onclick="switchView('timeline', this)">
        <span class="nav-num">05</span> Línea de tiempo
      </a>
      <a class="nav-item" onclick="switchView('reports', this)">
        <span class="nav-num">06</span> Reportes
      </a>
    </nav>

    <div class="sidebar-footer">
      <div>ENGINE: MEALY FSM V2.0</div>
      <div style="margin-top:4px; color:var(--text-muted);" id="sync-time">SYNC: --:--:--</div>
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
          <div class="fsm-state-title" id="banner-state-title">ESTADO: NORMAL</div>
          <div class="fsm-state-desc" id="banner-state-desc">Internet estable. Micro-throughput dentro de baseline.</div>
        </div>
        <div class="fsm-meta-group">
          <div class="fsm-meta-item">
            <div class="card-label">Ultima medicion</div>
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
          <div class="card-label">L0 Conectividad</div>
          <div class="card-value" id="dash-l0-val">-- ms</div>
          <div class="card-sub" id="dash-l0-sub">Target: Gateway / DNS</div>
        </div>
        <div class="card">
          <div class="card-label">L1 Micro-throughput</div>
          <div class="card-value" id="dash-l1-val">-- Mbps</div>
          <div class="card-sub" id="dash-l1-sub">Intervalo actual: 5 s</div>
        </div>
        <div class="card">
          <div class="card-label">Estado FSM Mealy</div>
          <div class="card-value" id="dash-fsm-val">NORMAL</div>
          <div class="card-sub" id="dash-fsm-sub">Accion: Muestreo 5s</div>
        </div>
      </div>

      <div class="card">
        <div class="card-label" style="margin-bottom:12px;">Actividad reciente (Audit Trail En Tiempo Real)</div>
        <table class="terminal-table">
          <thead>
            <tr>
              <th>HORA</th>
              <th>ORIGEN</th>
              <th>SIMBOLO / ESTADO</th>
              <th>VALOR REGISTRADO</th>
              <th>DETALLE / TRANSICION</th>
            </tr>
          </thead>
          <tbody id="dash-activity-body">
            <tr><td colspan="5" style="color:var(--text-muted);">Cargando telemetria...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Vista 2 — Estado FSM -->
    <div id="view-fsm" class="view-container">
      <div class="card">
        <div class="card-label" style="margin-bottom:16px;">Diagrama de Transicion Mealy FSM V2</div>
        <div class="fsm-diagram">
          <div class="fsm-node active node-NORMAL" id="node-NORMAL">
            <div class="node-title">NORMAL</div>
            <div class="node-sub">L1: 5 s</div>
          </div>
          <div class="fsm-connector">
            <div class="connector-label">a (anomalia)</div>
          </div>
          <div class="fsm-node node-SOSPECHA" id="node-SOSPECHA">
            <div class="node-title">SOSPECHA</div>
            <div class="node-sub">L1: 1 s</div>
          </div>
          <div class="fsm-connector">
            <div class="connector-label">a (confirmado)</div>
          </div>
          <div class="fsm-node node-CONFIRMANDO" id="node-CONFIRMANDO">
            <div class="node-title">CONFIRMANDO</div>
            <div class="node-sub">Fast &gt; Ookla</div>
          </div>
          <div class="fsm-connector">
            <div class="connector-label">co / cd</div>
          </div>
          <div class="fsm-node node-EVENTO" id="node-EVENTO">
            <div class="node-title">EVENTO</div>
            <div class="node-sub">Recuperacion 3/3</div>
          </div>
        </div>
      </div>

      <div class="grid-2">
        <div class="card">
          <div class="card-label">Detalles del Estado Actual</div>
          <div style="font-family:var(--font-mono); font-size:12px; margin-top:10px; display:flex; flex-direction:column; gap:8px;">
            <div>Estado canónico: <span id="fsm-detail-state" class="badge-tag tag-normal">NORMAL</span></div>
            <div>Frecuencia L1 adaptativa: <span id="fsm-detail-freq">5 segundos</span></div>
            <div>Contador de recuperacion: <span id="fsm-detail-rec">0 / 3</span></div>
            <div>Tiempo transcurrido: <span id="fsm-detail-time">00:00:00</span></div>
            <div>Ultimo simbolo recibido: <span id="fsm-detail-sym">n (lectura normal)</span></div>
          </div>
        </div>
        <div class="card">
          <div class="card-label">Siguiente Accion Programada</div>
          <div style="font-family:var(--font-mono); font-size:12px; margin-top:10px; color:var(--text-muted);" id="fsm-next-action">
            Muestreo continuo L1 a 5s. Sin sospecha de anomalia.
          </div>
        </div>
      </div>
    </div>

    <!-- Vista 3 — Sensores -->
    <div id="view-sensors" class="view-container">
      <div class="grid-2">
        <div class="card">
          <div class="card-label">L0 CONECTIVIDAD</div>
          <div style="font-family:var(--font-mono); font-size:12px; margin-top:10px; display:flex; flex-direction:column; gap:6px;">
            <div>Gateway: <span id="sen-l0-gw">[ OK ]</span></div>
            <div>DNS Resolution: <span id="sen-l0-dns">[ OK ]</span></div>
            <div>TCP Connection: <span id="sen-l0-tcp">[ OK ]</span></div>
            <div>HTTPS Reachability: <span id="sen-l0-http">[ OK ]</span></div>
            <div style="margin-top:8px; color:var(--text-muted);" id="sen-l0-ts">Ultima ejecucion: --:--:--</div>
          </div>
        </div>

        <div class="card">
          <div class="card-label">L1 MICRO-THROUGHPUT</div>
          <div style="font-family:var(--font-mono); font-size:12px; margin-top:10px; display:flex; flex-direction:column; gap:6px;">
            <div>Throughput: <span id="sen-l1-tp">-- Mbps</span></div>
            <div>Intervalo activo: <span id="sen-l1-int">5 s (NORMAL)</span></div>
            <div>Streams utilizados: <span id="sen-l1-str">4</span></div>
            <div>Tiempo medicion: <span id="sen-l1-ms">-- ms</span></div>
            <div style="margin-top:8px; color:var(--text-muted);" id="sen-l1-ts">Ultima ejecucion: --:--:--</div>
          </div>
        </div>
      </div>

      <div class="grid-2">
        <div class="card">
          <div class="card-label">FAST.COM (CONFIRMACION INTERMEDIA)</div>
          <div style="font-family:var(--font-mono); font-size:12px; margin-top:10px; display:flex; flex-direction:column; gap:6px;">
            <div>Estado de ejecucion: <span id="sen-fast-exec" class="badge-tag tag-blue">NO EJECUTADO</span></div>
            <div>Motivo: <span id="sen-fast-reason" style="color:var(--text-muted);">No necesario en estado NORMAL</span></div>
            <div>Ultima medicion: <span id="sen-fast-val">-- Mbps</span></div>
          </div>
        </div>

        <div class="card">
          <div class="card-label">OOKLA SPEEDTEST (CONFIRMACION PESADA FINAL)</div>
          <div style="font-family:var(--font-mono); font-size:12px; margin-top:10px; display:flex; flex-direction:column; gap:6px;">
            <div>Estado de ejecucion: <span id="sen-ookla-exec" class="badge-tag tag-blue">NO EJECUTADO</span></div>
            <div>Motivo: <span id="sen-ookla-reason" style="color:var(--text-muted);">No necesario sin confirmacion de Fast.com</span></div>
            <div>Ultima medicion: <span id="sen-ookla-val">-- Mbps</span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Vista 4 — Eventos -->
    <div id="view-events" class="view-container">
      <div class="filter-bar">
        <button class="filter-btn active" onclick="filterEvents('all', this)">[ Todos ]</button>
        <button class="filter-btn" onclick="filterEvents('degradation', this)">[ Degradacion ]</button>
        <button class="filter-btn" onclick="filterEvents('outage', this)">[ Caida total ]</button>
        <button class="filter-btn" onclick="filterEvents('active', this)">[ Activos ]</button>
      </div>

      <div id="events-list-container">
        <div class="card" style="color:var(--text-muted); font-family:var(--font-mono); font-size:12px;">Cargando registro de incidentes...</div>
      </div>
    </div>

    <!-- Vista 5 — Timeline -->
    <div id="view-timeline" class="view-container">
      <div class="card">
        <div class="card-label" style="margin-bottom:16px;">Linea de Tiempo Sincronizada (Mediciones Mbps + FSM + Evidencias)</div>
        <div class="chart-container">
          <canvas id="timelineChart"></canvas>
        </div>
      </div>
    </div>

    <!-- Vista 6 — Reportes -->
    <div id="view-reports" class="view-container">
      <div class="grid-4">
        <div class="card">
          <div class="card-label">Disponibilidad L0</div>
          <div class="card-value" id="rep-uptime">100.0%</div>
        </div>
        <div class="card">
          <div class="card-label">Degradaciones V2</div>
          <div class="card-value" id="rep-deg-count">0</div>
        </div>
        <div class="card">
          <div class="card-label">Caidas Totales</div>
          <div class="card-value" id="rep-out-count">0</div>
        </div>
        <div class="card">
          <div class="card-label">MTTR Promedio</div>
          <div class="card-value" id="rep-mttr">0s</div>
        </div>
      </div>

      <div class="card">
        <div class="card-label" style="margin-bottom:12px;">Exportacion de Registros V2</div>
        <div style="display:flex; gap:12px;">
          <button class="filter-btn" onclick="exportData('json')">[ Exportar JSON ]</button>
          <button class="filter-btn" onclick="exportData('csv')">[ Exportar CSV ]</button>
        </div>
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
      'sensors': 'OBSERVACION Y SENSORES',
      'events': 'REGISTRO DE INCIDENTES',
      'timeline': 'LINEA DE TIEMPO SINCRONIZADA',
      'reports': 'REPORTES Y EXPORTACION'
    };
    document.getElementById('view-title').textContent = titles[viewName] || 'CONTROLINTERNET V2';

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

      // FSM Nodes highlight
      ['NORMAL', 'SOSPECHA', 'CONFIRMANDO', 'EVENTO'].forEach(s => {
        const node = document.getElementById('node-' + s);
        if (node) {
          if (s === state) node.classList.add('active');
          else node.classList.remove('active');
        }
      });

      document.getElementById('fsm-detail-state').textContent = state;
      document.getElementById('fsm-detail-freq').textContent = interval + ' segundos';
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
        const symbolTag = `<span class="badge-tag tag-blue">${row.input_symbol}</span>`;
        
        tr.innerHTML = `
          <td>${time}</td>
          <td>FSM</td>
          <td>${symbolTag}</td>
          <td>${row.current_state} &rarr; ${row.next_state}</td>
          <td>${row.output_action || 'TRANSICION'}</td>
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
        document.getElementById('sen-l0-ts').textContent = 'Ultima ejecucion: ' + formatLocalTime(data.l0.last_reading.timestamp);
      }

      if (data.l1 && data.l1.last_reading) {
        document.getElementById('sen-l1-tp').textContent = data.l1.last_reading.throughput_mbps.toFixed(1) + ' Mbps';
        document.getElementById('sen-l1-int').textContent = data.l1.interval_seconds + ' s';
        document.getElementById('sen-l1-ts').textContent = 'Ultima ejecucion: ' + formatLocalTime(data.l1.last_reading.timestamp);
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
          <span class="badge-tag ${ev.end_time ? 'tag-normal' : 'tag-evento'}">${ev.end_time ? 'RECUPERADO' : 'ACTIVO'}</span>
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
            <div class="incident-prop-label">Duracion observada</div>
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
    else if (type === 'degradation') renderEvents(allEvents.filter(e => (e.event_type || '').includes('DEGRADACION')));
    else if (type === 'outage') renderEvents(allEvents.filter(e => (e.event_type || '').includes('CAIDA')));
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
            borderColor: '#58A6FF',
            backgroundColor: 'rgba(88, 166, 255, 0.1)',
            borderWidth: 2,
            tension: 0.2,
            fill: true
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { ticks: { color: '#8B949E', font: { family: 'IBM Plex Mono', size: 10 } }, grid: { color: '#1F2633' } },
            y: { ticks: { color: '#8B949E', font: { family: 'IBM Plex Mono', size: 10 } }, grid: { color: '#1F2633' } }
          },
          plugins: {
            legend: { labels: { color: '#E6EDF3', font: { family: 'IBM Plex Mono', size: 11 } } }
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

  // Periodic polling
  setInterval(() => {
    fetchStatus();
    fetchActivity();
    fetchSensors();
    if (currentView === 'events') fetchEvents();
  }, 3000);

  // Initial load
  fetchStatus();
  fetchActivity();
  fetchSensors();
  fetchEvents();
</script>
</body>
</html>
"""
