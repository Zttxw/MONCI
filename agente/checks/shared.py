"""Estado compartido entre loops asyncio del agente.

SpeedtestWindow coordina la captura de latencia bajo carga (bufferbloat)
entre connectivity_loop y speed_loop. Ver ARCHITECTURE.md para el protocolo.
"""

import asyncio
from dataclasses import dataclass, field


@dataclass
class SpeedtestWindow:
    """Ventana de medición de latencia durante un speedtest.

    Protocolo:
    1. speed_loop llama a start() justo antes de ejecutar el binario speedtest.
    2. connectivity_loop, en cada ciclo de ping, chequea is_active y si es True,
       llama a record_latency(ms) con la latencia del ping que ya ejecutó
       para la máquina de estados (cero tráfico adicional).
    3. speed_loop llama a finish() al terminar → retorna el promedio y resetea.

    Seguridad: asyncio es single-threaded (no hay paralelismo real), así que
    no hay condición de carrera entre las corrutinas. El Lock es defensa en
    profundidad por si se usa asyncio.to_thread en el futuro.
    """

    _active: bool = False
    _cooldown_until: float = 0.0
    _samples: list[float] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        """Marca inicio de ventana de speedtest. Limpia muestras previas."""
        async with self._lock:
            self._active = True
            self._samples.clear()

    @property
    def is_active(self) -> bool:
        """True mientras un speedtest está en ejecución."""
        return self._active

    @property
    def is_in_cooldown(self) -> bool:
        """True mientras el sistema se encuentra en la ventana de cooldown post-speedtest."""
        import time
        return time.monotonic() < self._cooldown_until

    async def start_cooldown(self, seconds: float = 60.0) -> None:
        """Activa la ventana de descanso (cooldown) tras finalizar el test oficial."""
        import time
        async with self._lock:
            self._cooldown_until = time.monotonic() + seconds

    async def record_latency(self, latency_ms: float) -> None:
        """Registra una muestra de latencia si la ventana está activa."""
        async with self._lock:
            if self._active:
                self._samples.append(latency_ms)

    async def finish(self) -> float | None:
        """Cierra la ventana y retorna el promedio de latencia, o None si no hay muestras."""
        async with self._lock:
            self._active = False
            if not self._samples:
                return None
            avg = sum(self._samples) / len(self._samples)
            self._samples.clear()
            return round(avg, 3)
