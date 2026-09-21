"""Implementación de V2Repository para SQLite.

Encapsula todas las operaciones de lectura/escritura en las tablas V2 de SQLite.
"""

import json
import logging
from datetime import datetime
from typing import Optional

import db
from engine.models import (
    L0Reading,
    L1Reading,
    L2Reading,
    TransitionRecord,
)
from storage.base import V2Repository


logger = logging.getLogger(__name__)


class SQLiteV2Repository(V2Repository):
    """Repositorio SQLite para la arquitectura V2."""

    def save_l0_reading(self, reading: L0Reading) -> None:
        db.insert_v2_l0_reading(
            timestamp=reading.timestamp,
            is_reachable=reading.is_reachable,
            target=reading.target,
            latency_ms=reading.latency_ms,
            packet_loss_pct=reading.packet_loss_pct,
            sub_checks=reading.sub_checks,
        )

    def save_l1_reading(self, reading: L1Reading) -> None:
        db.insert_v2_l1_reading(
            timestamp=reading.timestamp,
            throughput_mbps=reading.throughput_mbps,
            total_time_ms=reading.total_time_ms,
            dns_ms=reading.dns_ms,
            tcp_ms=reading.tcp_ms,
            tls_ms=reading.tls_ms,
            ttfb_ms=reading.ttfb_ms,
            transfer_ms=reading.transfer_ms,
            streams_used=reading.streams_used,
            baseline_mbps=reading.baseline_mbps,
            is_degraded=reading.is_degraded,
            is_valid=reading.is_valid,
        )

    def save_l2_reading(self, reading: L2Reading) -> None:
        db.insert_v2_l2_reading(
            timestamp=reading.timestamp,
            download_mbps=reading.download_mbps,
            upload_mbps=reading.upload_mbps,
            ping_ms=reading.ping_ms,
            loaded_latency_ms=reading.loaded_latency_ms,
            baseline_mbps=reading.baseline_mbps,
            server_id=reading.server_id,
            server_name=reading.server_name,
        )

    def save_fsm_transition(self, record: TransitionRecord) -> None:
        db.insert_v2_fsm_transition(
            timestamp=record.timestamp,
            current_state=record.current_state.value,
            input_symbol=record.input_symbol.value,
            next_state=record.next_state.value,
            output_action=record.output_action.value,
            is_reentry=record.is_reentry,
            readings_json=record.readings_json,
        )

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
        return db.insert_v2_event(
            event_type=event_type,
            state_origin=state_origin,
            start_time=start_time,
            l0_status=l0_status,
            l1_throughput=l1_throughput,
            l2_download=l2_download,
            diagnosis_code=diagnosis_code,
            diagnosis_detail=diagnosis_detail,
            evidence=evidence,
        )

    def close_v2_event(
        self,
        event_id: int,
        fin: datetime,
        diagnosis_code: Optional[str] = None,
        diagnosis_detail: Optional[dict] = None,
        evidence: Optional[dict] = None,
    ) -> None:
        db.close_v2_event(
            event_id=event_id,
            fin=fin,
            diagnosis_code=diagnosis_code,
            diagnosis_detail=diagnosis_detail,
            evidence=evidence,
        )

    def get_active_v2_event(self) -> Optional[dict]:
        return db.get_active_v2_event()

    def get_last_fsm_transition(self) -> Optional[dict]:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM v2_fsm_history ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if row:
                return dict(row)
        return None

    def get_v2_fsm_history(self, limit: int = 50) -> list[dict]:
        return db.get_v2_fsm_history(limit=limit)

    def get_v2_events(self, limit: int = 50) -> list[dict]:
        return db.get_v2_events(limit=limit)
