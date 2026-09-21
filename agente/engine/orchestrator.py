"""Orquestador del Motor de Detección V2.

Coordina los ciclos de muestreo de los sensores L0, L1 y L2, ejecuta la discretización,
sincroniza las transiciones en la FSM Mealy, dispara el test L2 en segundo plano
y delega la persistencia al V2Repository.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from checks.shared import SpeedtestWindow
from engine.diagnosis import diagnose_event
from engine.discretizer import Discretizer
from engine.fsm import MealyFSM
from engine.models import (
    FsmState,
    InputSymbol,
    OutputAction,
    L0Reading,
    L1Reading,
    L2Reading,
)
from sensors.l0 import L0Sensor
from sensors.l1 import L1Sensor
from sensors.l2 import L2Sensor
from storage.base import V2Repository
from storage.sqlite import SQLiteV2Repository


logger = logging.getLogger(__name__)


class V2Orchestrator:
    """Orquestador principal del motor V2 con inyección de repositorio y recuperación de estado."""

    def __init__(
        self,
        window: Optional[SpeedtestWindow] = None,
        repository: Optional[V2Repository] = None,
    ):
        self.window = window or SpeedtestWindow()
        self.repository = repository or SQLiteV2Repository()

        self.fsm = MealyFSM(initial_state=FsmState.NORMAL)
        self.discretizer = Discretizer()

        # Sensores V2
        self.l0_sensor = L0Sensor()
        self.l1_sensor = L1Sensor()
        self.l2_sensor = L2Sensor(window=self.window)

        # Evento y candado de disparo para L2 (previene ejecuciones concurrentes)
        self.l2_trigger_event = asyncio.Event()
        self.l2_is_running = False

        # Últimas lecturas en memoria
        self.latest_l0: Optional[L0Reading] = None
        self.latest_l1: Optional[L1Reading] = None
        self.latest_l2: Optional[L2Reading] = None

        # Seguimiento del evento activo V2
        self.active_event_start: Optional[datetime] = None

    def restore_state(self) -> None:
        """Recupera de forma consistente el estado de la FSM desde el repositorio tras un reinicio.

        Contempla e informa inconsistencias entre v2_events y v2_fsm_history aplicando reglas deterministas.
        """
        logger.info("V2 Orquestador: Iniciando recuperación de estado FSM desde la base de datos...")

        active_event = self.repository.get_active_v2_event()
        last_fsm = self.repository.get_last_fsm_transition()

        if active_event:
            event_id = active_event["id"]
            event_type = active_event["event_type"]
            start_time = datetime.fromisoformat(active_event["start_time"])

            self.fsm.current_state = FsmState.EVENTO
            self.fsm.active_event_id = event_id
            self.fsm.active_event_type = event_type
            self.active_event_start = start_time

            if last_fsm and last_fsm["next_state"] != FsmState.EVENTO.value:
                logger.warning(
                    "INCONSISTENCIA DETECTADA: Evento activo id=%d existe pero FSM marcaba %s. "
                    "Restableciendo FSM a estado EVENTO.",
                    event_id, last_fsm["next_state"]
                )

            logger.info("FSM reanudada en estado EVENTO (id=%d, tipo=%s, inicio=%s)", event_id, event_type, start_time.isoformat())

        elif last_fsm:
            last_state_str = last_fsm["next_state"]
            try:
                last_state = FsmState(last_state_str)
            except ValueError:
                last_state = FsmState.NORMAL

            if last_state == FsmState.EVENTO:
                logger.warning(
                    "INCONSISTENCIA DETECTADA: Historial FSM indicaba EVENTO pero no hay evento activo en DB. "
                    "Restableciendo FSM a estado NORMAL."
                )
                self.fsm.current_state = FsmState.NORMAL
            elif last_state in (FsmState.SOSPECHA, FsmState.CONFIRMANDO):
                self.fsm.current_state = last_state
                logger.info("FSM reanudada en estado %s desde el historial.", last_state.value)
                if last_state == FsmState.CONFIRMANDO:
                    logger.info("FSM reanudada en CONFIRMANDO — activando disparo L2 de re-verificación.")
                    self.l2_trigger_event.set()
            else:
                self.fsm.current_state = FsmState.NORMAL
                logger.info("FSM iniciada en estado NORMAL.")

        else:
            self.fsm.current_state = FsmState.NORMAL
            logger.info("Base de datos limpia: FSM iniciada en estado NORMAL.")

    async def _l2_worker_loop(self) -> None:
        """Worker en segundo plano para L2 con candado de concurrencia e aislamiento de excepciones."""
        logger.info("V2 Orquestador: Worker de disparo L2 iniciado.")
        while True:
            await self.l2_trigger_event.wait()
            self.l2_trigger_event.clear()

            if self.l2_is_running:
                logger.warning("V2 Orquestador: Intento de disparo L2 omitido — ejecución anterior aún en curso.")
                continue

            try:
                self.l2_is_running = True
                logger.info("V2 Orquestador: Ejecutando Sensor L2 (Ookla Speedtest)...")
                l2_reading = await self.l2_sensor.measure()

                if l2_reading:
                    self.latest_l2 = l2_reading
                    self.repository.save_l2_reading(l2_reading)

                    # Si seguimos en CONFIRMANDO, forzar un tick inmediato con la evidencia L2
                    if self.fsm.current_state == FsmState.CONFIRMANDO:
                        logger.info("V2 Orquestador: L2 finalizó exitosamente — ejecutando tick inmediato en CONFIRMANDO.")
                        await self.tick()
            except Exception as e:
                logger.error("Error no controlado en worker L2: %s", e, exc_info=True)
            finally:
                self.l2_is_running = False

    async def tick(self, now: Optional[datetime] = None) -> None:
        """Ejecuta una iteración de muestreo con aislamiento de fallas entre sensores."""
        ref_time = now or datetime.now(timezone.utc)

        # 1. Medir L0 con captura defensiva de excepciones
        try:
            l0_reading = await self.l0_sensor.measure()
            self.latest_l0 = l0_reading
            if l0_reading is not None:
                self.repository.save_l0_reading(l0_reading)
        except Exception as e:
            logger.error("Error en Sensor L0 durante tick: %s", e, exc_info=True)
            self.latest_l0 = None
            l0_reading = None

        # 2. Medir L1 con captura defensiva de excepciones
        try:
            is_cooldown_active = self.window.is_in_cooldown
            l1_reading = await self.l1_sensor.measure(is_cooldown_active=is_cooldown_active)
            self.latest_l1 = l1_reading
            if l1_reading is not None:
                self.repository.save_l1_reading(l1_reading)
        except Exception as e:
            logger.error("Error en Sensor L1 durante tick: %s", e, exc_info=True)
            self.latest_l1 = None
            l1_reading = None

        old_state_val = self.fsm.current_state

        # 3. Discretizar evidencias -> InputSymbol
        symbol = self.discretizer.evaluate(
            l0=self.latest_l0,
            l1=self.latest_l1,
            l2=self.latest_l2,
            current_state=old_state_val,
            event_start_time=self.active_event_start,
            now=ref_time,
        )

        # REGLA ESTRICTA: falta de evidencia != n. Si el discretizador retorna None, se omite la transición FSM.
        if symbol is None:
            logger.info("V2 Orquestador: Sin evidencia suficiente para emitir símbolo FSM. Tick omitido.")
            return

        readings_summary = {
            "l0": {
                "reachable": l0_reading.is_reachable if l0_reading else None,
                "latency_ms": l0_reading.latency_ms if l0_reading else None,
                "loss_pct": l0_reading.packet_loss_pct if l0_reading else None,
            },
            "l1": {
                "throughput_mbps": l1_reading.throughput_mbps if l1_reading else None,
                "baseline_mbps": l1_reading.baseline_mbps if l1_reading else None,
                "degraded": l1_reading.is_degraded if l1_reading else None,
                "valid": l1_reading.is_valid if l1_reading else None,
                "bytes_downloaded": getattr(l1_reading, "bytes_downloaded", None) if l1_reading else None,
            },
            "l2": {
                "download_mbps": self.latest_l2.download_mbps if self.latest_l2 else None,
                "ping_ms": self.latest_l2.ping_ms if self.latest_l2 else None,
            } if self.latest_l2 else None,
            "fsm": {
                "recovery_counter": self.fsm.recovery_counter,
                "recovery_k": self.fsm.recovery_k,
            },
        }

        # 4. Procesar símbolo en FSM Mealy
        record = self.fsm.process_symbol(symbol, timestamp=ref_time, readings_json=readings_summary)

        # Guardar registro de transición en la base de datos a través del repositorio
        self.repository.save_fsm_transition(record)

        # Disparar L2 únicamente en la transición de entrada a CONFIRMANDO
        if old_state_val != FsmState.CONFIRMANDO and record.next_state == FsmState.CONFIRMANDO:
            logger.info("V2 Orquestador: Transición a CONFIRMANDO detectada — activando disparo L2.")
            self.l2_trigger_event.set()

        # 5. Manejar Acciones de Salida (OutputAction)
        await self._handle_action(record.output_action, record.next_state, readings_summary, ref_time)

    async def _handle_action(
        self,
        action: OutputAction,
        next_state: FsmState,
        readings_summary: dict,
        now: datetime,
    ) -> None:
        """Aplica las acciones asociadas a las transiciones FSM."""

        if action == OutputAction.ABRIR_EVENTO_TOTAL:
            if self.fsm.active_event_id is None:
                diag_code, diag_detail = diagnose_event(self.latest_l0, self.latest_l1, self.latest_l2)
                event_id = self.repository.open_v2_event(
                    event_type="caida_total",
                    state_origin=next_state.value,
                    start_time=now,
                    l0_status="OFFLINE",
                    l1_throughput=self.latest_l1.throughput_mbps if self.latest_l1 else None,
                    l2_download=self.latest_l2.download_mbps if self.latest_l2 else None,
                    diagnosis_code=diag_code,
                    diagnosis_detail=diag_detail,
                    evidence=readings_summary,
                )
                self.fsm.active_event_id = event_id
                self.fsm.active_event_type = "caida_total"
                self.active_event_start = now
                logger.info("V2 Evento Caída Total abierto id=%d", event_id)

        elif action == OutputAction.ABRIR_EVENTO_DEGRADACION:
            if self.fsm.active_event_id is None:
                diag_code, diag_detail = diagnose_event(self.latest_l0, self.latest_l1, self.latest_l2)
                event_id = self.repository.open_v2_event(
                    event_type="degradacion_velocidad",
                    state_origin=next_state.value,
                    start_time=now,
                    l0_status="ONLINE" if (self.latest_l0 and self.latest_l0.is_reachable) else "OFFLINE",
                    l1_throughput=self.latest_l1.throughput_mbps if self.latest_l1 else None,
                    l2_download=self.latest_l2.download_mbps if self.latest_l2 else None,
                    diagnosis_code=diag_code,
                    diagnosis_detail=diag_detail,
                    evidence=readings_summary,
                )
                self.fsm.active_event_id = event_id
                self.fsm.active_event_type = "degradacion_velocidad"
                self.active_event_start = now
                logger.info("V2 Evento Degradación abierto id=%d", event_id)

        elif action == OutputAction.CERRAR_EVENTO:
            if self.fsm.active_event_id is not None:
                diag_code, diag_detail = diagnose_event(self.latest_l0, self.latest_l1, self.latest_l2)
                self.repository.close_v2_event(
                    event_id=self.fsm.active_event_id,
                    fin=now,
                    diagnosis_code=diag_code,
                    diagnosis_detail=diag_detail,
                    evidence=readings_summary,
                )
                logger.info("V2 Evento cerrado id=%d", self.fsm.active_event_id)
                self.fsm.active_event_id = None
                self.fsm.active_event_type = None
                self.active_event_start = None

    async def run_loop(self, interval_seconds: int = 30) -> None:
        """Loop principal del orquestador V2."""
        logger.info("V2 Orquestador iniciado — intervalo de tick: %ds", interval_seconds)
        
        # Restaurar estado FSM desde la base de datos antes de iniciar el ciclo
        self.restore_state()

        # Lanzar worker de disparo L2
        asyncio.create_task(self._l2_worker_loop())

        while True:
            try:
                await self.tick()
            except Exception as e:
                logger.error("Error en tick de V2 Orquestador: %s", e, exc_info=True)

            await asyncio.sleep(interval_seconds)
