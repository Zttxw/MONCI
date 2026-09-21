"""Discretizador de Lecturas Multicapa para el Motor V2.

Transforma lecturas continuas e independientes de los sensores L0, L1 y L2
en los símbolos discretos aceptados por la máquina de estados FSM Mealy:

  n  -> evidencia SALUDABLE y FRESCA de L0 y L1
  a  -> anomalía en sensor liviano L1 (throughput degradado)
  co -> caída total en L0 (incomunicación confirmada)
  cd -> degradación de velocidad confirmada por sensor oficial L2
  d  -> descarte/rechazo explícito de L2 durante CONFIRMANDO

REGLA FUNDAMENTAL DE DISEÑO:
  Falta de evidencia ≠ Evidencia Saludable
  Si L0 o L1 no disponen de lecturas válidas y frescas, el discretizador retorna None
  (SIN_EVIDENCIA), impidiendo falsas recuperaciones a NORMAL o saltos de estado erróneos.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from config import SENSOR_FRESHNESS_SECONDS
from engine.models import L0Reading, L1Reading, L2Reading, InputSymbol, FsmState


logger = logging.getLogger(__name__)


class Discretizer:
    """Discretiza el vector de evidencias {L0, L1, L2} a un símbolo de entrada FSM."""

    @staticmethod
    def is_fresh(timestamp: datetime, now: Optional[datetime] = None) -> bool:
        """Verifica si la lectura está dentro del tiempo de frescura tolerado."""
        current = now or datetime.now(timezone.utc)
        age = (current - timestamp).total_seconds()
        return age <= SENSOR_FRESHNESS_SECONDS

    def evaluate(
        self,
        l0: Optional[L0Reading],
        l1: Optional[L1Reading],
        l2: Optional[L2Reading],
        current_state: FsmState = FsmState.NORMAL,
        event_start_time: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> Optional[InputSymbol]:
        """Evalúa las fuentes de evidencia y retorna InputSymbol o None si no hay evidencia fresca.

        Prioridades:
        1. Caída Total L0 (L0 presente, fresco y no alcanzable) -> InputSymbol.CO
        2. Evaluación de L2 (si L2 está fresco):
           - L2 degradado -> InputSymbol.CD (confirma degradación)
           - L2 sano en CONFIRMANDO -> InputSymbol.D (descarte/rechazo de sospecha)
        3. Anomalía L1 (L1 presente, fresco, válido y degradado) -> InputSymbol.A
        4. Evidencia Saludable (L0 presente, fresco, alcanzable Y L1 presente, fresco, válido, sano) -> InputSymbol.N
        5. Falta de evidencia suficiente -> None (SIN_EVIDENCIA)
        """
        ref_time = now or datetime.now(timezone.utc)

        # Validar frescura estricta de lecturas
        l0_fresh = l0 is not None and self.is_fresh(l0.timestamp, ref_time)
        l1_fresh = l1 is not None and l1.is_valid and self.is_fresh(l1.timestamp, ref_time)
        l2_fresh = l2 is not None and self.is_fresh(l2.timestamp, ref_time)

        # 1. Prioridad 1: Caída de Conectividad en L0
        if l0_fresh and not l0.is_reachable:
            logger.info("Discretizador: L0 incomunicado -> 'co'")
            return InputSymbol.CO

        # 2. Prioridad 2: Evaluación de Sensor L2
        if l2_fresh:
            if l2.baseline_mbps and l2.baseline_mbps > 0:
                is_l2_degraded = l2.download_mbps < (l2.baseline_mbps * 0.5)
            else:
                is_l2_degraded = l2.download_mbps < 100.0  # Umbral fallback provisional

            if is_l2_degraded:
                logger.info("Discretizador: L2 confirma degradación -> 'cd'")
                return InputSymbol.CD
            elif current_state == FsmState.CONFIRMANDO:
                logger.info("Discretizador: L2 sano descarta sospecha en CONFIRMANDO -> 'd'")
                return InputSymbol.D

        # 3. Prioridad 3: Anomalía detectada en L1
        if l1_fresh and l1.is_degraded:
            logger.info("Discretizador: L1 reporta degradación -> 'a'")
            return InputSymbol.A

        # 4. Evaluación de Evidencia Saludable (L0 y L1 DEBEN ESTAR PRESENTES Y FRESCOS)
        l0_ok = l0_fresh and l0.is_reachable
        l1_ok = l1_fresh and not l1.is_degraded

        if l0_ok and (not l1 or l1_ok):
            logger.info("Discretizador: Evidencia saludable de sensores -> 'n'")
            return InputSymbol.N

        # 5. Si falta evidencia o los datos están obsoletos: NO ASUMIR SALUDABLE
        logger.warning(
            "Discretizador: Sin evidencia fresca suficiente (L0 fresh=%s, L1 fresh=%s). Transición omitida.",
            l0_fresh, l1_fresh
        )
        return None
