from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.cards.constants import AssignmentMethod, CardStatus


class CreateScenario(StrEnum):
    L1 = "l1"
    L2_SELF = "l2_self"
    L2_URGENT = "l2_urgent"
    L2_RETROACTIVE = "l2_retroactive"


@dataclass(frozen=True)
class RoleCreatePlan:
    status: CardStatus
    l2_engineer_id: int | None
    assignment_method_code: int
    urgency_code: int
    urgent_reason: str | None
    retroactive_flag: bool
    actual_start_at: datetime | None
    actual_end_at: datetime | None
    result_code: int | None
    engineer_report: str | None


def validate_role_create(
    *,
    scenario: CreateScenario,
    planned_start_at: datetime,
    planned_duration_minutes: int,
    urgent_reason: str | None = None,
    result_code: int | None = None,
    engineer_report: str | None = None,
    now: datetime | None = None,
) -> RoleCreatePlan:
    if planned_start_at.tzinfo is None or planned_start_at.utcoffset() is None:
        raise ValueError("planned_start_at_must_be_timezone_aware")
    now = now or datetime.now(UTC)
    start = planned_start_at.astimezone(UTC)
    end = start + timedelta(minutes=planned_duration_minutes)

    if scenario in {CreateScenario.L1, CreateScenario.L2_SELF}:
        _validate_normal_window(start=start, now=now)
    elif scenario == CreateScenario.L2_URGENT:
        if start < now:
            raise ValueError("urgent_planned_start_must_not_be_in_past")
        _validate_horizon(start=start, now=now)
        if not (urgent_reason or "").strip():
            raise ValueError("urgent_reason_required")
    elif scenario == CreateScenario.L2_RETROACTIVE:
        if start >= now:
            raise ValueError("retroactive_planned_start_must_be_in_past")
        if end <= now:
            if result_code is None:
                raise ValueError("retroactive_result_required")
            if not (engineer_report or "").strip():
                raise ValueError("retroactive_engineer_report_required")
            return RoleCreatePlan(
                status=CardStatus.COMPLETED,
                l2_engineer_id=None,
                assignment_method_code=int(AssignmentMethod.RETROACTIVE),
                urgency_code=0,
                urgent_reason=None,
                retroactive_flag=True,
                actual_start_at=start,
                actual_end_at=end,
                result_code=result_code,
                engineer_report=engineer_report.strip(),
            )
        return RoleCreatePlan(
            status=CardStatus.IN_PROGRESS,
            l2_engineer_id=None,
            assignment_method_code=int(AssignmentMethod.RETROACTIVE),
            urgency_code=0,
            urgent_reason=None,
            retroactive_flag=True,
            actual_start_at=start,
            actual_end_at=None,
            result_code=None,
            engineer_report=None,
        )
    else:  # pragma: no cover - enum guards public callers
        raise ValueError("unsupported_create_scenario")

    if scenario == CreateScenario.L1:
        return RoleCreatePlan(
            status=CardStatus.CREATED,
            l2_engineer_id=None,
            assignment_method_code=int(AssignmentMethod.AUTO),
            urgency_code=0,
            urgent_reason=None,
            retroactive_flag=False,
            actual_start_at=None,
            actual_end_at=None,
            result_code=None,
            engineer_report=None,
        )
    return RoleCreatePlan(
        status=CardStatus.ASSIGNED,
        l2_engineer_id=None,
        assignment_method_code=int(AssignmentMethod.L2_SELF),
        urgency_code=1 if scenario == CreateScenario.L2_URGENT else 0,
        urgent_reason=(urgent_reason or "").strip()
        if scenario == CreateScenario.L2_URGENT
        else None,
        retroactive_flag=False,
        actual_start_at=None,
        actual_end_at=None,
        result_code=None,
        engineer_report=None,
    )


def validate_reschedule_window(
    *,
    planned_start_at: datetime,
    urgency_code: int,
    now: datetime | None = None,
) -> None:
    """Apply the create scheduling window to a time change.

    Urgent cards retain the create contract's exemption from the 120-minute
    minimum while still requiring a future, timezone-aware start within the
    14-day horizon.
    """
    if planned_start_at.tzinfo is None or planned_start_at.utcoffset() is None:
        raise ValueError("planned_start_at_must_be_timezone_aware")
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now_must_be_timezone_aware")
    start = planned_start_at.astimezone(UTC)
    now = now.astimezone(UTC)
    if urgency_code > 0:
        if start < now:
            raise ValueError("urgent_planned_start_must_not_be_in_past")
        _validate_horizon(start=start, now=now)
    else:
        _validate_normal_window(start=start, now=now)


def _validate_normal_window(*, start: datetime, now: datetime) -> None:
    if start < now + timedelta(minutes=120):
        raise ValueError("planned_start_too_soon")
    _validate_horizon(start=start, now=now)


def _validate_horizon(*, start: datetime, now: datetime) -> None:
    if start > now + timedelta(days=14):
        raise ValueError("planned_start_too_far")
