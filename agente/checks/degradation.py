"""Módulo de evaluación de degradación de velocidad.

Mantiene la máquina de estados de degradación para las fuentes 'oficial' (Ookla) y 'liviano' (Cloudflare probe).
Evalúa la velocidad medida contra el baseline (promedio de las últimas 20 mediciones oficiales sanas).
"""

import logging
from datetime import datetime, timezone

from config import CRITICAL_THRESHOLD_PERCENT, DEGRADATION_THRESHOLD_PERCENT
from db import (
    close_evento_degradacion,
    get_baseline_mbps_liviano,
    get_baseline_mbps_oficial,
    get_open_evento_degradacion,
    insert_evento_degradacion,
)

logger = logging.getLogger(__name__)


def evaluate_degradation(velocidad_mbps: float, fuente: str) -> None:
    """Evalúa la velocidad medida contra el baseline propio de su fuente para detectar o cerrar degradaciones.

    Args:
        velocidad_mbps: Velocidad de descarga medida en Mbps.
        fuente: 'oficial' | 'liviano'
    """
    if fuente == "liviano":
        baseline = get_baseline_mbps_liviano(limit=20, min_count=5)
    else:
        baseline = get_baseline_mbps_oficial(limit=20, min_count=20)

    if not baseline or baseline <= 0:
        logger.info(
            "Sin baseline de velocidad suficiente para evaluar degradación (fuente=%s) — acumulando mediciones",
            fuente,
        )
        return


    porcentaje = (velocidad_mbps / baseline) * 100.0
    now = datetime.now(timezone.utc)

    # Determinar severidad según el umbral
    target_severidad = None
    if porcentaje < CRITICAL_THRESHOLD_PERCENT:
        target_severidad = "critica"
    elif porcentaje < DEGRADATION_THRESHOLD_PERCENT:
        target_severidad = "degradada"

    open_event = get_open_evento_degradacion(fuente)

    if target_severidad is not None:
        # Hay degradación
        if open_event is None:
            # Abrir nuevo evento
            insert_evento_degradacion(
                severidad=target_severidad,
                fuente=fuente,
                baseline_mbps=baseline,
                velocidad_mbps=velocidad_mbps,
                inicio=now,
            )
        elif open_event["severidad"] != target_severidad:
            # Cambio de nivel de severidad (ej. degradada -> critica): cerrar viejo y abrir nuevo
            close_evento_degradacion(open_event["id"], now)
            insert_evento_degradacion(
                severidad=target_severidad,
                fuente=fuente,
                baseline_mbps=baseline,
                velocidad_mbps=velocidad_mbps,
                inicio=now,
            )
    else:
        # Velocidad dentro del rango normal
        if open_event is not None:
            logger.info(
                "Velocidad recuperada a %.2f Mbps (%.1f%% del baseline %.2f Mbps) — cerrando degradación id=%d",
                velocidad_mbps, porcentaje, baseline, open_event["id"],
            )
            close_evento_degradacion(open_event["id"], now)
