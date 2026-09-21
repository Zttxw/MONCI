"""Evaluador de Causa Raíz para el Motor V2.

Analiza el vector completo de evidencia de los 3 sensores (L0, L1, L2) al momento de
abrir o cerrar un evento para clasificar la causa raíz con un código formal y un detalle estructurado.

Códigos de Diagnóstico:
- ISP_FALLA_TOTAL: Alcance L0 caído totalmente.
- ISP_DEGRADACION_LAST_MILE: Caída de velocidad generalizada sin fallas de DNS/Ping.
- PROBLEMA_DNS_LOCAL: Falla de resolución DNS o latencia DNS extrema en L1.
- PROBLEMA_LATENCIA_CARGA_BUFFERBLOAT: Alta latencia bajo carga observada en L2.
- ISP_LATENCIA_SERVICIOS: TTFB o TLS handshake degradado en L1.
- DEGRADACION_DESCONOCIDA: Evento no categorizado por reglas específicas.
"""

import logging
from typing import Optional

from engine.models import L0Reading, L1Reading, L2Reading


logger = logging.getLogger(__name__)


def diagnose_event(
    l0: Optional[L0Reading] = None,
    l1: Optional[L1Reading] = None,
    l2: Optional[L2Reading] = None,
) -> tuple[str, dict]:
    """Diagnostica la causa raíz basándose en el vector de lecturas {L0, L1, L2}.

    Retorna una tupla (diagnosis_code, diagnosis_detail_dict).
    """
    detail = {
        "l0_reachable": l0.is_reachable if l0 else None,
        "l0_packet_loss": l0.packet_loss_pct if l0 else None,
        "l1_throughput_mbps": l1.throughput_mbps if l1 else None,
        "l1_baseline_mbps": l1.baseline_mbps if l1 else None,
        "l1_dns_ms": l1.dns_ms if l1 else None,
        "l1_tcp_ms": l1.tcp_ms if l1 else None,
        "l1_ttfb_ms": l1.ttfb_ms if l1 else None,
        "l2_download_mbps": l2.download_mbps if l2 else None,
        "l2_loaded_latency_ms": l2.loaded_latency_ms if l2 else None,
    }

    # 1. Falla Total de Conectividad
    if l0 and not l0.is_reachable:
        code = "ISP_FALLA_TOTAL"
        detail["summary"] = "Incomunicación total detectada por sensor L0."
        return code, detail

    # 2. Problema de DNS Local / Resolución
    if l0 and l0.sub_checks:
        dns_fails = [k for k, v in l0.sub_checks.items() if k.startswith("dns_") and not v.get("ok")]
        if dns_fails:
            code = "PROBLEMA_DNS_LOCAL"
            detail["summary"] = f"Falla de resolución DNS detectada en L0 ({', '.join(dns_fails)})."
            return code, detail

    if l1 and l1.dns_ms and l1.dns_ms > 200.0:
        code = "PROBLEMA_DNS_LOCAL"
        detail["summary"] = f"Alta latencia de DNS en L1 ({l1.dns_ms:.1f}ms)."
        return code, detail

    # 3. Bufferbloat / Latencia Bajo Carga Elevada
    if l2 and l2.loaded_latency_ms and l2.loaded_latency_ms > 150.0:
        code = "PROBLEMA_LATENCIA_CARGA_BUFFERBLOAT"
        detail["summary"] = f"Alta latencia bajo carga observada durante Ookla Speedtest ({l2.loaded_latency_ms:.1f}ms)."
        return code, detail

    # 4. Latencia de Servicios / TTFB / SSL Handshake
    if l1 and l1.ttfb_ms and l1.ttfb_ms > 500.0:
        code = "ISP_LATENCIA_SERVICIOS"
        detail["summary"] = f"Tiempo hasta el primer byte (TTFB) elevado ({l1.ttfb_ms:.1f}ms)."
        return code, detail

    # 5. Degradación de Última Milla (Throughput / Descarga)
    if (l2 and l2.baseline_mbps and l2.download_mbps < l2.baseline_mbps * 0.5) or \
       (l1 and l1.baseline_mbps and l1.throughput_mbps < l1.baseline_mbps * 0.5):
        code = "ISP_DEGRADACION_LAST_MILE"
        detail["summary"] = "Throughput significativamente inferior al baseline sin fallas de conectividad básica."
        return code, detail

    # Fallback por defecto
    code = "DEGRADACION_DESCONOCIDA"
    detail["summary"] = "Anomalía o degradación detectada sin causa específica atribuible."
    return code, detail
