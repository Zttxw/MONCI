"""Clase base abstracta para los sensores V2.

Define la interfaz común para L0 (conectividad), L1 (probe liviano) y L2 (speedtest).
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


class BaseSensor(ABC):
    """Interfaz abstracta de sensor para el motor V2."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def measure(self) -> Optional[Any]:
        """Ejecuta una medición asíncrona y retorna la estructura de lectura del sensor."""
        pass
