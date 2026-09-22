"""Motor de Confirmación y Evidencia V2 (ConfirmationEngine).

Evalúa las evidencias producidas por los sensores (Fast.com y Ookla Speedtest)
y genera un resultado de confirmación (ConfirmationOutcome).

DESACOPLE ESTRICTO:
- NO modifica directamente la FSM Mealy V2.
- NO llama a fsm.process_symbol().
- Retorna un ConfirmationOutcome al V2Orchestrator para que este emita el InputSymbol correspondiente.
"""

import logging
from enum import Enum
from typing import Optional

from config import (
    FAST_DEGRADATION_THRESHOLD_PCT,
    FAST_DEFAULT_BASELINE_MBPS,
    FAST_MIN_BASELINE_SAMPLES,
    L2_DEGRADATION_THRESHOLD_PERCENT,
)
from engine.models import FastReading, L2Reading

logger = logging.getLogger(__name__)


class ConfirmationOutcome(str, Enum):
    """Resultados internos del proceso de confirmación de evidencia."""
    FAST_REJECT = "FAST_REJECT"             # Evidencia adicional insuficiente para confirmar degradación
    FAST_CONFIRM = "FAST_CONFIRM"           # Condición compatible con degradación (habilita prueba Ookla)
    OOKLA_REJECT = "OOKLA_REJECT"           # Ookla descarta degradación
    OOKLA_CONFIRM = "OOKLA_CONFIRM"         # Ookla confirma degradación
    CONFIRMATION_ERROR = "CONFIRMATION_ERROR" # Fallo de sensor / error de red
    MEASUREMENT_DISCREPANCY = "MEASUREMENT_DISCREPANCY" # Discrepancia metodológica o de ruta entre sensores


class ConfirmationEngine:
    """Motor de evaluación de evidencia intermedia y confirmación pesada."""

    def __init__(
        self,
        fast_threshold_pct: float = FAST_DEGRADATION_THRESHOLD_PCT,
        fast_default_baseline: float = FAST_DEFAULT_BASELINE_MBPS,
        fast_min_samples: int = FAST_MIN_BASELINE_SAMPLES,
        l2_threshold_pct: float = L2_DEGRADATION_THRESHOLD_PERCENT,
    ):
        self.fast_threshold_pct = fast_threshold_pct
        self.fast_default_baseline = fast_default_baseline
        self.fast_min_samples = fast_min_samples
        self.l2_threshold_pct = l2_threshold_pct

    def evaluate_fast(self, reading: Optional[FastReading], baseline_mbps: Optional[float] = None) -> ConfirmationOutcome:
        """Evalúa la evidencia proporcionada por Fast.com.

        - Si reading es inválido o tuvo error -> CONFIRMATION_ERROR.
        - Si throughput < (baseline * threshold_pct) -> FAST_CONFIRM (compatible con degradación).
        - De lo contrario -> FAST_REJECT (evidencia no fue suficiente para confirmar degradación).
        """
        if reading is None or not reading.is_valid or reading.error is not None:
            logger.warning("ConfirmationEngine: Lectura Fast inválida o con error: %s", reading.error if reading else "None")
            return ConfirmationOutcome.CONFIRMATION_ERROR

        effective_baseline = baseline_mbps if (baseline_mbps and baseline_mbps > 0) else self.fast_default_baseline
        threshold_val = effective_baseline * self.fast_threshold_pct

        is_degraded = reading.throughput_mbps < threshold_val
        reading.baseline_mbps = effective_baseline
        reading.is_degraded = is_degraded

        logger.info(
            "ConfirmationEngine Fast: throughput=%.2f Mbps (baseline=%.2f Mbps, threshold=%.2f Mbps) -> %s",
            reading.throughput_mbps, effective_baseline, threshold_val,
            "FAST_CONFIRM" if is_degraded else "FAST_REJECT"
        )

        if is_degraded:
            return ConfirmationOutcome.FAST_CONFIRM
        else:
            return ConfirmationOutcome.FAST_REJECT

    def evaluate_ookla(self, reading: Optional[L2Reading], baseline_mbps: Optional[float] = None) -> ConfirmationOutcome:
        """Evalúa la evidencia proporcionada por Ookla Speedtest (L2).

        - Si reading es inválido -> CONFIRMATION_ERROR.
        - Si download_mbps < (baseline * l2_threshold_pct / 100.0) -> OOKLA_CONFIRM.
        - De lo contrario -> OOKLA_REJECT.
        """
        if reading is None:
            logger.warning("ConfirmationEngine: Lectura Ookla L2 nula.")
            return ConfirmationOutcome.CONFIRMATION_ERROR

        effective_baseline = baseline_mbps if (baseline_mbps and baseline_mbps > 0) else 800.0
        threshold_val = effective_baseline * (self.l2_threshold_pct / 100.0)

        is_degraded = reading.download_mbps < threshold_val
        reading.baseline_mbps = effective_baseline

        logger.info(
            "ConfirmationEngine Ookla: download=%.2f Mbps (baseline=%.2f Mbps, threshold=%.2f Mbps) -> %s",
            reading.download_mbps, effective_baseline, threshold_val,
            "OOKLA_CONFIRM" if is_degraded else "OOKLA_REJECT"
        )

        if is_degraded:
            return ConfirmationOutcome.OOKLA_CONFIRM
        else:
            return ConfirmationOutcome.OOKLA_REJECT
