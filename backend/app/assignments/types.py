from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time


@dataclass(frozen=True)
class ScheduleWindow:
    weekday: int
    start_time: time
    end_time: time
    timezone: str
    valid_from: date | None
    valid_to: date | None


@dataclass(frozen=True)
class TimeInterval:
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class L2DistributionCandidate:
    user_id: int
    schedules: tuple[ScheduleWindow, ...]
    absences: tuple[TimeInterval, ...]
    active_cards: tuple[TimeInterval, ...]


@dataclass(frozen=True)
class AssignmentCycleRecord:
    id: int
    card_id: int
    cycle_number: int
    status_code: int


@dataclass(frozen=True)
class AssignmentAttemptRecord:
    id: int
    cycle_id: int
    card_id: int
    l2_engineer_id: int
    status_code: int
    responded_at: datetime | None = None
    actor_user_id: int | None = None
    rejection_reason: str | None = None
