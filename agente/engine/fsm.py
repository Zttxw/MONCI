"""Máquina de Estados Mealy para el motor de detección V2.

Implementa las transiciones explícitas entre estados FSM:
  NORMAL, SOSPECHA, CONFIRMANDO, EVENTO

Símbolos de entrada (InputSymbol):
  n  -> evidencia saludable (L0 reachable, L1 no degradado)
  a  -> evidencia anómala en L1
  co -> caída total L0
  cd -> degradación L2 confirmada
  d  -> L2 descarta/rechaza sospecha en CONFIRMANDO

Acciones de salida (OutputAction):
  NADA, REGISTRAR_SOSPECHA, DISPARAR_L2, ABRIR_EVENTO_TOTAL,
  ABRIR_EVENTO_DEGRADACION, REGISTRAR_EVIDENCIA, REFRESCAR_EVENTO,
  REFRESCAR_NORMAL, CERRAR_EVENTO

Reglas de recuperación:
- En EVENTO, se requiere un número `FSM_RECOVERY_K` (default 3) de lecturas saludables
  consecutivas (símbolo 'n') para transicionar a NORMAL y cerrar el evento.
- Si llega una lectura anómala ('a', 'co', 'cd') durante EVENTO, `recovery_counter` se reinicia a 0.
- 'd' representa el descarte/rechazo de L2 durante CONFIRMANDO (`CONFIRMANDO + d -> NORMAL`).
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from config import FSM_RECOVERY_K
from engine.models import FsmState, InputSymbol, OutputAction, FsmSnapshot, TransitionRecord


logger = logging.getLogger(__name__)


# Tabla de Transiciones Base Mealy
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

    # Desde CONFIRMANDO (CONFIRMANDO + n / a permanecen en CONFIRMANDO; CONFIRMANDO + d regresa a NORMAL)
    (FsmState.CONFIRMANDO, InputSymbol.N): (FsmState.CONFIRMANDO, OutputAction.REGISTRAR_EVIDENCIA),
    (FsmState.CONFIRMANDO, InputSymbol.A): (FsmState.CONFIRMANDO, OutputAction.REGISTRAR_EVIDENCIA),
    (FsmState.CONFIRMANDO, InputSymbol.CO): (FsmState.EVENTO, OutputAction.ABRIR_EVENTO_TOTAL),
    (FsmState.CONFIRMANDO, InputSymbol.CD): (FsmState.EVENTO, OutputAction.ABRIR_EVENTO_DEGRADACION),
    (FsmState.CONFIRMANDO, InputSymbol.D): (FsmState.NORMAL, OutputAction.REFRESCAR_NORMAL),
}


class MealyFSM:
    """Máquina de Estados Finita Mealy para la detección V2 con recuento de recuperación (recovery_counter)."""

    def __init__(self, initial_state: FsmState = FsmState.NORMAL, recovery_k: int = FSM_RECOVERY_K):
        self.current_state = initial_state
        self.active_event_id: Optional[int] = None
        self.active_event_type: Optional[str] = None
        self.sospecha_start: Optional[datetime] = None
        self.recovery_counter: int = 0
        self.recovery_k: int = recovery_k

    def process_symbol(
        self,
        symbol: InputSymbol,
        timestamp: Optional[datetime] = None,
        readings_json: Optional[dict] = None,
    ) -> TransitionRecord:
        """Procesa un símbolo de entrada y actualiza el estado FSM considerando recovery_counter."""
        now = timestamp or datetime.now(timezone.utc)
        old_state = self.current_state
        is_reentry = False

        # Manejo especial de recuperación progresiva en estado EVENTO
        if old_state == FsmState.EVENTO:
            if symbol == InputSymbol.N:
                self.recovery_counter += 1
                logger.info("FSM EVENTO: Lectura sana recibida. recovery_counter = %d/%d", self.recovery_counter, self.recovery_k)
                if self.recovery_counter >= self.recovery_k:
                    new_state = FsmState.NORMAL
                    action = OutputAction.CERRAR_EVENTO
                    self.recovery_counter = 0
                else:
                    new_state = FsmState.EVENTO
                    action = OutputAction.REFRESCAR_EVENTO

            elif symbol == InputSymbol.D:
                # Cierre inmediato por descarte/confirmación explícita
                new_state = FsmState.NORMAL
                action = OutputAction.CERRAR_EVENTO
                self.recovery_counter = 0

            else:
                # Símbolos anómalos ('a', 'co', 'cd') en EVENTO -> Reiniciar contador de recuperación
                self.recovery_counter = 0
                new_state = FsmState.EVENTO
                action = OutputAction.REFRESCAR_EVENTO

                if symbol in (InputSymbol.CO, InputSymbol.CD):
                    if (symbol == InputSymbol.CO and self.active_event_type == "caida_total") or \
                       (symbol == InputSymbol.CD and self.active_event_type == "degradacion_velocidad"):
                        is_reentry = True

        else:
            # Para estados NORMAL, SOSPECHA, CONFIRMANDO usar tabla de transiciones estática
            key = (old_state, symbol)
            if key not in TRANSITION_TABLE:
                logger.error("Transición no definida en FSM: estado=%s simbolo=%s", old_state, symbol)
                new_state, action = old_state, OutputAction.NADA
            else:
                new_state, action = TRANSITION_TABLE[key]

        # Actualizar contadores y temporizadores según el cambio de estado
        if new_state == FsmState.SOSPECHA and old_state != FsmState.SOSPECHA:
            self.sospecha_start = now

        if new_state == FsmState.NORMAL:
            self.sospecha_start = None
            self.recovery_counter = 0

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
            "FSM Step: %s + '%s' -> %s [Acción: %s, recovery_counter=%d/%d%s]",
            old_state.value,
            symbol.value,
            new_state.value,
            action.value,
            self.recovery_counter,
            self.recovery_k,
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
            recovery_counter=self.recovery_counter,
        )
