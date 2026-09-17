from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ConfirmedOmnideskReschedule:
    """Trusted, normalized fact produced by a future Omnidesk adapter."""

    card_id: int
    source_event_id: str
    planned_start_at: datetime
    planned_duration_minutes: int

    def __post_init__(self) -> None:
        if self.card_id <= 0 or not self.source_event_id.strip():
            raise ValueError("invalid_omnidesk_reschedule_event")
        if self.planned_start_at.tzinfo is None or self.planned_start_at.utcoffset() is None:
            raise ValueError("planned_start_at_must_be_timezone_aware")
        if not 30 <= self.planned_duration_minutes <= 720:
            raise ValueError("planned_duration_minutes_out_of_range")
