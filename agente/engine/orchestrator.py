import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import db
from checks.shared import SpeedtestWindow
from config import (
    L1_NORMAL_INTERVAL_SECONDS,
    L1_SUSPECT_INTERVAL_SECONDS,
    L0_INTERVAL_SECONDS,
)
from engine.confirmation import ConfirmationEngine, ConfirmationOutcome
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
    FastReading,
)
from sensors.fast import FastSensor
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
        self.confirmation_engine = ConfirmationEngine()

        # Sensores V2
        self.l0_sensor = L0Sensor()
        self.l1_sensor = L1Sensor()
        self.fast_sensor = FastSensor()
        self.l2_sensor = L2Sensor(window=self.window)

        # Evento y candado de disparo para la secuencia de confirmación
        self.confirmation_trigger_event = asyncio.Event()
        self.confirmation_is_running = False

        # Temporizador desacoplado de L0 (L0 se ejecuta cada L0_INTERVAL_SECONDS independientemente de la frecuencia de L1)
        self.last_l0_mono = 0.0

        # Alias de retrocompatibilidad
        self.l2_trigger_event = self.confirmation_trigger_event

        # Últimas lecturas en memoria
        self.latest_l0: Optional[L0Reading] = None
        self.latest_l1: Optional[L1Reading] = None
        self.latest_fast: Optional[FastReading] = None
        self.latest_l2: Optional[L2Reading] = None

        # Seguimiento del evento activo V2
        self.active_event_start: Optional[datetime] = None

    def get_current_interval(self) -> float:
        """Retorna el intervalo dinámico según el estado FSM (5s en NORMAL, 1s en SOSPECHA)."""
        if self.fsm.current_state == FsmState.SOSPECHA:
            return L1_SUSPECT_INTERVAL_SECONDS
        return L1_NORMAL_INTERVAL_SECONDS

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
                    logger.info("FSM reanudada en CONFIRMANDO — activando secuencia de confirmación.")
                    self.confirmation_trigger_event.set()
            else:
                self.fsm.current_state = FsmState.NORMAL
                logger.info("FSM iniciada en estado NORMAL.")

        else:
            self.fsm.current_state = FsmState.NORMAL
            logger.info("Base de datos limpia: FSM iniciada en estado NORMAL.")

    def _build_readings_summary(self, ref_time: datetime, discrepancy: Optional[str] = None) -> dict:
        """Construye un resumen estandarizado de lecturas para auditoría FSM con trazabilidad temporal."""
        return {
            "l0": {
                "reachable": self.latest_l0.is_reachable if self.latest_l0 else None,
                "latency_ms": self.latest_l0.latency_ms if self.latest_l0 else None,
                "loss_pct": self.latest_l0.packet_loss_pct if self.latest_l0 else None,
            },
            "l1": {
                "throughput_mbps": self.latest_l1.throughput_mbps if self.latest_l1 else None,
                "baseline_mbps": self.latest_l1.baseline_mbps if self.latest_l1 else None,
                "degraded": self.latest_l1.is_degraded if self.latest_l1 else None,
                "valid": self.latest_l1.is_valid if self.latest_l1 else None,
                "bytes_downloaded": getattr(self.latest_l1, "bytes_downloaded", None) if self.latest_l1 else None,
            },
            "fast": {
                "throughput_mbps": self.latest_fast.throughput_mbps if self.latest_fast else None,
                "baseline_mbps": self.latest_fast.baseline_mbps if self.latest_fast else None,
                "degraded": self.latest_fast.is_degraded if self.latest_fast else None,
                "valid": self.latest_fast.is_valid if self.latest_fast else None,
                "duration_ms": self.latest_fast.duration_ms if self.latest_fast else None,
            } if self.latest_fast else None,
            "l2": {
                "download_mbps": self.latest_l2.download_mbps if self.latest_l2 else None,
                "ping_ms": self.latest_l2.ping_ms if self.latest_l2 else None,
            } if self.latest_l2 else None,
            "fsm": {
                "current_state": self.fsm.current_state.value,
                "sospecha_start": self.fsm.sospecha_start.isoformat() if self.fsm.sospecha_start else None,
                "active_event_start": self.active_event_start.isoformat() if self.active_event_start else None,
                "recovery_counter": self.fsm.recovery_counter,
                "recovery_k": self.fsm.recovery_k,
            },
            "traceability": {
                "first_anomaly_time": self.fsm.sospecha_start.isoformat() if self.fsm.sospecha_start else None,
                "confirmed_at": self.active_event_start.isoformat() if self.active_event_start else None,
            },
            "discrepancy": discrepancy,
        }

    async def _confirmation_worker_loop(self) -> None:
        """Worker en segundo plano para la secuencia de confirmación (Fast -> Ookla)."""
        logger.info("V2 Orquestador: Worker de confirmación (Fast -> Ookla) iniciado.")
        while True:
            await self.confirmation_trigger_event.wait()
            self.confirmation_trigger_event.clear()

            if self.confirmation_is_running:
                logger.warning("V2 Orquestador: Secuencia de confirmación omitida — ejecución anterior en curso.")
                continue

            try:
                self.confirmation_is_running = True
                logger.info("V2 Orquestador: Iniciando etapa intermedia Fast.com...")

                # 1. Ejecutar Fast.com
                fast_reading = await self.fast_sensor.measure()
                if fast_reading:
                    self.latest_fast = fast_reading
                    fast_baseline = db.get_baseline_mbps_fast()
                    fast_reading.baseline_mbps = fast_baseline
                    self.repository.save_fast_reading(fast_reading)

                # 2. Evaluar evidencia Fast con ConfirmationEngine
                fast_outcome = self.confirmation_engine.evaluate_fast(
                    fast_reading,
                    fast_reading.baseline_mbps if fast_reading else None
                )

                if fast_outcome == ConfirmationOutcome.FAST_REJECT:
                    logger.info("V2 Orquestador: Fast REJECT — Evidencia insuficiente para confirmar degradación. FSM retorna a NORMAL ('d'). NO se ejecuta Ookla.")
                    if self.fsm.current_state == FsmState.CONFIRMANDO:
                        now = datetime.now(timezone.utc)
                        summary = self._build_readings_summary(now, discrepancy="L1 anómalo, Fast saludable (no confirmación)")
                        record = self.fsm.process_symbol(InputSymbol.D, timestamp=now, readings_json=summary)
                        self.repository.save_fsm_transition(record)
                        await self._handle_action(record.output_action, record.next_state, summary, now)
                    continue

                elif fast_outcome in (ConfirmationOutcome.FAST_CONFIRM, ConfirmationOutcome.CONFIRMATION_ERROR):
                    if fast_outcome == ConfirmationOutcome.CONFIRMATION_ERROR:
                        logger.warning("V2 Orquestador: Fast finalizó con ERROR — Procediendo defensivamente a Ookla para verificación.")
                    else:
                        logger.info("V2 Orquestador: Fast CONFIRM — Condición compatible con degradación. Procediendo a prueba pesada Ookla (L2).")

                    # 3. Ejecutar Ookla Speedtest (L2)
                    l2_reading = await self.l2_sensor.measure()
                    if l2_reading:
                        self.latest_l2 = l2_reading
                        self.repository.save_l2_reading(l2_reading)

                        ookla_baseline = db.get_baseline_mbps_oficial()
                        ookla_outcome = self.confirmation_engine.evaluate_ookla(
                            l2_reading,
                            ookla_baseline
                        )

                        if self.fsm.current_state == FsmState.CONFIRMANDO:
                            now = datetime.now(timezone.utc)
                            if ookla_outcome == ConfirmationOutcome.OOKLA_CONFIRM:
                                logger.info("V2 Orquestador: Ookla CONFIRM — Transicionando FSM a EVENTO ('cd').")
                                summary = self._build_readings_summary(now)
                                record = self.fsm.process_symbol(InputSymbol.CD, timestamp=now, readings_json=summary)
                                self.repository.save_fsm_transition(record)
                                await self._handle_action(record.output_action, record.next_state, summary, now)
                            else:
                                logger.info("V2 Orquestador: Ookla REJECT — FSM retorna a NORMAL ('d'). Discrepancia registrada.")
                                summary = self._build_readings_summary(now, discrepancy="L1/Fast anómalos, Ookla saludable (no confirmación)")
                                record = self.fsm.process_symbol(InputSymbol.D, timestamp=now, readings_json=summary)
                                self.repository.save_fsm_transition(record)
                                await self._handle_action(record.output_action, record.next_state, summary, now)

            except Exception as e:
                logger.error("Error no controlado en worker de confirmación: %s", e, exc_info=True)
            finally:
                self.confirmation_is_running = False

    async def tick(self, now: Optional[datetime] = None) -> None:
        """Ejecuta una iteración de muestreo con aislamiento de fallas entre sensores y desacople de frecuencia L0/L1."""
        ref_time = now or datetime.now(timezone.utc)
        now_mono = time.monotonic()

        # 1. Medir L0 de forma desacoplada (únicamente cada L0_INTERVAL_SECONDS)
        if self.last_l0_mono == 0.0 or (now_mono - self.last_l0_mono) >= L0_INTERVAL_SECONDS:
            try:
                l0_reading = await self.l0_sensor.measure()
                self.latest_l0 = l0_reading
                self.last_l0_mono = now_mono
                if l0_reading is not None:
                    self.repository.save_l0_reading(l0_reading)
            except Exception as e:
                logger.error("Error en Sensor L0 durante tick: %s", e, exc_info=True)
                self.latest_l0 = None

        # 2. Medir L1 (frecuencia alta adaptativa: 5s en NORMAL, 1s en SOSPECHA)
        try:
            is_cooldown_active = self.window.is_in_cooldown
            l1_reading = await self.l1_sensor.measure(is_cooldown_active=is_cooldown_active)
            self.latest_l1 = l1_reading
            if l1_reading is not None:
                self.repository.save_l1_reading(l1_reading)
        except Exception as e:
            logger.error("Error en Sensor L1 durante tick: %s", e, exc_info=True)
            self.latest_l1 = None

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

        readings_summary = self._build_readings_summary(ref_time)

        # 4. Procesar símbolo en FSM Mealy
        record = self.fsm.process_symbol(symbol, timestamp=ref_time, readings_json=readings_summary)

        # Guardar registro de transición en la base de datos a través del repositorio
        self.repository.save_fsm_transition(record)

        # Disparar secuencia de confirmación únicamente al entrar en CONFIRMANDO
        if old_state_val != FsmState.CONFIRMANDO and record.next_state == FsmState.CONFIRMANDO:
            logger.info("V2 Orquestador: Transición a CONFIRMANDO detectada — activando secuencia de confirmación (Fast -> Ookla).")
            self.confirmation_trigger_event.set()

        # 5. Manejar Acciones de Salida (OutputAction)
        await self._handle_action(record.output_action, record.next_state, readings_summary, ref_time)

    async def _handle_action(
        self,
        action: OutputAction,
        next_state: FsmState,
        readings_summary: dict,
        now: datetime,
    ) -> None:
        """Aplica las acciones asociadas a las transiciones FSM con trazabilidad temporal explícita."""

        if action == OutputAction.ABRIR_EVENTO_TOTAL:
            if self.fsm.active_event_id is None:
                first_anomaly = self.fsm.sospecha_start or now
                readings_summary["traceability"] = {
                    "first_anomaly_time": first_anomaly.isoformat(),
                    "confirmed_at": now.isoformat(),
                }
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
                logger.info("V2 Evento Caída Total abierto id=%d (first_anomaly=%s, confirmed_at=%s)",
                            event_id, first_anomaly.isoformat(), now.isoformat())

        elif action == OutputAction.ABRIR_EVENTO_DEGRADACION:
            if self.fsm.active_event_id is None:
                first_anomaly = self.fsm.sospecha_start or now
                readings_summary["traceability"] = {
                    "first_anomaly_time": first_anomaly.isoformat(),
                    "confirmed_at": now.isoformat(),
                }
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
                logger.info("V2 Evento Degradación abierto id=%d (first_anomaly=%s, confirmed_at=%s)",
                            event_id, first_anomaly.isoformat(), now.isoformat())

        elif action == OutputAction.CERRAR_EVENTO:
            if self.fsm.active_event_id is not None:
                # recovered_at corresponde exactamente al instante de la tercera lectura saludable que completa 3/3 recovery
                recovered_at = now
                readings_summary["traceability"] = {
                    "first_anomaly_time": self.fsm.sospecha_start.isoformat() if self.fsm.sospecha_start else None,
                    "confirmed_at": self.active_event_start.isoformat() if self.active_event_start else None,
                    "recovered_at": recovered_at.isoformat(),
                }
                diag_code, diag_detail = diagnose_event(self.latest_l0, self.latest_l1, self.latest_l2)
                self.repository.close_v2_event(
                    event_id=self.fsm.active_event_id,
                    fin=recovered_at,
                    diagnosis_code=diag_code,
                    diagnosis_detail=diag_detail,
                    evidence=readings_summary,
                )
                logger.info("V2 Evento cerrado id=%d en recovered_at=%s (3/3 lectura sana)",
                            self.fsm.active_event_id, recovered_at.isoformat())
                self.fsm.active_event_id = None
                self.fsm.active_event_type = None
                self.active_event_start = None

    async def run_loop(self, interval_seconds: Optional[float] = None) -> None:
        """Loop principal del orquestador V2 con muestreo adaptativo L1 y compensación de drift temporal."""
        logger.info(
            "V2 Orquestador iniciado — muestreo adaptativo L1 (NORMAL: %.1fs, SOSPECHA: %.1fs)",
            L1_NORMAL_INTERVAL_SECONDS, L1_SUSPECT_INTERVAL_SECONDS
        )
        
        # Restaurar estado FSM desde la base de datos antes de iniciar el ciclo
        self.restore_state()

        # Lanzar worker de confirmación (Fast -> Ookla)
        asyncio.create_task(self._confirmation_worker_loop())

        while True:
            tick_start_mono = time.monotonic()
            try:
                await self.tick()
            except Exception as e:
                logger.error("Error en tick de V2 Orquestador: %s", e, exc_info=True)

            # Determinar el intervalo dinámico (o usar el estático si se pasó explícitamente en tests)
            target_interval = interval_seconds if interval_seconds is not None else self.get_current_interval()
            tick_duration = time.monotonic() - tick_start_mono
            sleep_remaining = max(0.0, target_interval - tick_duration)
            await asyncio.sleep(sleep_remaining)
