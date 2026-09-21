"""Máquina de Estados Mealy para el motor de detección V2.

Implementa las transiciones explícitas entre estados FSM:
  NORMAL, SOSPECHA, CONFIRMANDO, EVENTO

Símbolos de entrada (InputSymbol):
  n (normal), a (anomalía), co (caída L0), cd (degradación L2), d (normalización)

Acciones de salida (OutputAction):
  NADA, REGISTRAR_SOSPECHA, DISPARAR_L2, ABRIR_EVENTO_TOTAL,
  ABRIR_EVENTO_DEGRADACION, REGISTRAR_EVIDENCIA, REFRESCAR_EVENTO,
  REFRESCAR_NORMAL, CERRAR_EVENTO

Reglas especiales incorporadas:
- CONFIRMANDO soporta entradas n y a permaneciendo en CONFIRMANDO (User Correction #1).
- Disparo de L2 únicamente en la TRANSICIÓN hacia CONFIRMANDO (User Correction #2).
- Reentrada basada en coincidencia de tipo entre evento activo y nueva lectura (User Correction #7).
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from engine.models import FsmState, InputSymbol, OutputAction, FsmSnapshot, TransitionRecord


logger = logging.getLogger(__name__)


# Tabla de Transiciones FSM Mealy: (estado_actual, simbolo_entrada) -> (nuevo_estado, accion_salida)
TRANSITION_TABLE: dict[Tuple[FsmState, InputSymbol], Tuple[FsmState, OutputAction]] = {
    # Desde NORMAL
    (FsmState.NORMAL, InputSymbol.N): (FsmState.NORMAL, OutputAction.NADA),
    (FsmState.NORMAL, InputSymbol.A): (FsmState.SOSPECHA, OutputAction.REGISTRAR_SOSPECHA),
    (FsmState.NORMAL, InputSymbol.CO): (FsmState.EVENTO, OutputAction.ABRIR_EVENTO_TOTAL),
    (FsmState.NORMAL, InputSymbol.CD): (FsmState.EVENTO, OutputAction.ABRIR_EVENTO_DEGRADACION),
    (FsmState.NORMAL, InputSymbol.D): (FsmState.NORMAL, OutputAction.NADA),

    # Desde SOSPECHA
    (FsmState.SOSPECHA, InputSymbol.N): (FsmState.NORMAL, OutputAction.REFRESCAR_NORMAL),
    (FsmState.SOSPECHA, InputSymbol.A): (FsmState.CONFIRMANDO, OutputAction.DISPARAR_L2),
    (FsmState.SOSPECHA, InputSymbol.CO): (FsmState.EVENTO, OutputAction.ABRIR_EVENTO_TOTAL),
    (FsmState.SOSPECHA, InputSymbol.CD): (FsmState.EVENTO, OutputAction.ABRIR_EVENTO_DEGRADACION),
    (FsmState.SOSPECHA, InputSymbol.D): (FsmState.NORMAL, OutputAction.REFRESCAR_NORMAL),

    # Desde CONFIRMANDO (User Correction #1: n y a se mantienen en CONFIRMANDO registrando evidencia)
    (FsmState.CONFIRMANDO, InputSymbol.N): (FsmState.CONFIRMANDO, OutputAction.REGISTRAR_EVIDENCIA),
    (FsmState.CONFIRMANDO, InputSymbol.A): (FsmState.CONFIRMANDO, OutputAction.REGISTRAR_EVIDENCIA),
    (FsmState.CONFIRMANDO, InputSymbol.CO): (FsmState.EVENTO, OutputAction.ABRIR_EVENTO_TOTAL),
    (FsmState.CONFIRMANDO, InputSymbol.CD): (FsmState.EVENTO, OutputAction.ABRIR_EVENTO_DEGRADACION),
    (FsmState.CONFIRMANDO, InputSymbol.D): (FsmState.NORMAL, OutputAction.REFRESCAR_NORMAL),

    # Desde EVENTO
    (FsmState.EVENTO, InputSymbol.N): (FsmState.EVENTO, OutputAction.REFRESCAR_EVENTO),
    (FsmState.EVENTO, InputSymbol.A): (FsmState.EVENTO, OutputAction.REFRESCAR_EVENTO),
    (FsmState.EVENTO, InputSymbol.CO): (FsmState.EVENTO, OutputAction.REFRESCAR_EVENTO),
    (FsmState.EVENTO, InputSymbol.CD): (FsmState.EVENTO, OutputAction.REFRESCAR_EVENTO),
    (FsmState.EVENTO, InputSymbol.D): (FsmState.NORMAL, OutputAction.CERRAR_EVENTO),
}


class MealyFSM:
    """Máquina de Estados Finita Mealy para la detección de eventos V2.

    Mantiene el estado actual del detector y genera registros de transición (TransitionRecord).
    """

    def __init__(self, initial_state: FsmState = FsmState.NORMAL):
        self.current_state = initial_state
        self.active_event_id: Optional[int] = None
        self.active_event_type: Optional[str] = None  # 'caida_total' | 'degradacion_velocidad'
        self.sospecha_start: Optional[datetime] = None

    def process_symbol(
        self,
        symbol: InputSymbol,
        timestamp: Optional[datetime] = None,
        readings_json: Optional[dict] = None,
    ) -> TransitionRecord:
        """Procesa un símbolo de entrada y actualiza el estado de la FSM.

        Retorna un TransitionRecord inmutable con la información del paso.
        """
        now = timestamp or datetime.now(timezone.utc)
        old_state = self.current_state

        # Buscar la transición en la tabla
        key = (old_state, symbol)
        if key not in TRANSITION_TABLE:
            logger.error("Transición no definida en FSM: estado=%s simbolo=%s", old_state, symbol)
            # Fallback seguro: mantener estado actual y sin acción
            new_state, action = old_state, OutputAction.NADA
        else:
            new_state, action = TRANSITION_TABLE[key]

        # Evaluación de reentrada en EVENTO (User Correction #7)
        # La reentrada ocurre si ya estamos en EVENTO y llega un símbolo anómalo (co o cd)
        # del mismo tipo que el evento que ya está abierto.
        is_reentry = False
        if old_state == FsmState.EVENTO and symbol in (InputSymbol.CO, InputSymbol.CD):
            if (symbol == InputSymbol.CO and self.active_event_type == "caida_total") or \
               (symbol == InputSymbol.CD and self.active_event_type == "degradacion_velocidad"):
                is_reentry = True

        # Actualizar variables internas del FSM según el nuevo estado y la acción
        if new_state == FsmState.SOSPECHA and old_state != FsmState.SOSPECHA:
            self.sospecha_start = now

        if new_state == FsmState.NORMAL:
            self.sospecha_start = None

        self.current_state = new_state

        record = TransitionRecord(
            timestamp=now,
            current_state=old_state,
            input_symbol=symbol,
            next_state=new_state,
            output_action=action,
            is_reentry=is_reentry,
            readings_json=readings_json,
        )

        logger.info(
            "FSM Step: %s + '%s' -> %s [Acción: %s%s]",
            old_state.value,
            symbol.value,
            new_state.value,
            action.value,
            " (Reentrada)" if is_reentry else "",
        )

        return record

    def get_snapshot(self, last_check: Optional[datetime] = None) -> FsmSnapshot:
        """Retorna una captura del estado actual de la FSM."""
        return FsmSnapshot(
            state=self.current_state,
            active_event_id=self.active_event_id,
            sospecha_start=self.sospecha_start,
            last_check=last_check or datetime.now(timezone.utc),
        )
