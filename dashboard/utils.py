"""Utilidades del dashboard — conversión de zona horaria de consultas."""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from config import TZ

local_tz = ZoneInfo(TZ)


def parse_local_date_range_to_utc(desde_str: str, hasta_str: str) -> tuple[str, str]:
    """Convierte un rango de fechas local (YYYY-MM-DD) a timestamps ISO en UTC.

    Ejemplo para America/Lima (UTC-5):
    desde = "2026-09-11" -> 2026-09-11 00:00:00 -05:00 -> 2026-09-11T05:00:00+00:00
    hasta = "2026-09-11" -> 2026-09-11 23:59:59.999999 -05:00 -> 2026-09-12T04:59:59.999999+00:00
    """
    try:
        d_start = datetime.strptime(desde_str[:10], "%Y-%m-%d").date()
    except Exception:
        d_start = datetime(2020, 1, 1).date()

    try:
        d_end = datetime.strptime(hasta_str[:10], "%Y-%m-%d").date()
    except Exception:
        d_end = datetime(2099, 12, 31).date()

    dt_start_local = datetime.combine(d_start, time.min, tzinfo=local_tz)
    dt_end_local = datetime.combine(d_end, time.max, tzinfo=local_tz)

    dt_start_utc = dt_start_local.astimezone(timezone.utc)
    dt_end_utc = dt_end_local.astimezone(timezone.utc)

    return dt_start_utc.isoformat(), dt_end_utc.isoformat()
