"""Interfaz abstracta V2Repository para desacoplar el motor de detección de la base de datos.

Permite sustituir SQLite por PostgreSQL o un servicio remoto en la arquitectura SaaS
sin modificar la lógica de la FSM ni del Orquestador.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Any

from engine.models import (
    L0Reading,
    L1Reading,
    L2Reading,
    FastReading,
    TransitionRecord,
)


class V2Repository(ABC):
    """Interfaz abstracta de repositorio de persistencia V2."""

    @abstractmethod
    def save_l0_reading(self, reading: L0Reading) -> None:
        """Persiste una lectura del Sensor L0."""
        pass

    @abstractmethod
    def save_l1_reading(self, reading: L1Reading) -> None:
        """Persiste una lectura del Sensor L1."""
        pass

    @abstractmethod
    def save_l2_reading(self, reading: L2Reading) -> None:
        """Persiste una lectura del Sensor L2."""
        pass

    @abstractmethod
    def save_fast_reading(self, reading: Optional[FastReading]) -> None:
        """Persiste una lectura del Sensor Fast.com."""
        pass

    @abstractmethod
    def save_fsm_transition(self, record: TransitionRecord) -> None:
        """Persiste un registro de transición/paso de la FSM (audit trail)."""
        pass

    @abstractmethod
    def open_v2_event(
        self,
        event_type: str,
        state_origin: str,
        start_time: datetime,
        l0_status: Optional[str] = None,
        l1_throughput: Optional[float] = None,
        l2_download: Optional[float] = None,
        diagnosis_code: Optional[str] = None,
        diagnosis_detail: Optional[dict] = None,
        evidence: Optional[dict] = None,
    ) -> int:
        """Abre un nuevo evento V2 y retorna su ID."""
        pass

    @abstractmethod
    def close_v2_event(
        self,
        event_id: int,
        fin: datetime,
        diagnosis_code: Optional[str] = None,
        diagnosis_detail: Optional[dict] = None,
        evidence: Optional[dict] = None,
    ) -> None:
        """Cierra un evento V2 activo."""
        pass

    @abstractmethod
    def get_active_v2_event(self) -> Optional[dict]:
        """Retorna el evento V2 activo (is_active = 1) en la base de datos, si existe."""
        pass

    @abstractmethod
    def get_last_fsm_transition(self) -> Optional[dict]:
        """Retorna la última transición registrada en el historial de la FSM."""
        pass

    @abstractmethod
    def get_v2_fsm_history(self, limit: int = 50) -> list[dict]:
        """Retorna los últimos N registros del historial FSM."""
        pass

    @abstractmethod
    def get_v2_events(self, limit: int = 50) -> list[dict]:
        """Retorna los últimos N eventos V2."""
        pass
