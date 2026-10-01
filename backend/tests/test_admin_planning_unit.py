from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.admin.planning_settings import (
    DEFAULT_PLANNING_SETTINGS,
    PlanningSettings,
    get_planning_settings,
)
from app.api.admin_planning import (
    AbsenceCreateRequest,
    DistributionMembershipUpdateRequest,
    PlanningSettingsUpdateRequest,
    ScheduleItem,
    _check_schedule_overlaps,
)
from app.cards.create_policy import (
    CreateScenario,
    validate_reschedule_window,
    validate_role_create,
)
from app.manager_create import validate_manager_window


def test_planning_settings_defaults_and_validation() -> None:
    settings = PlanningSettings()
    assert settings.min_lead_minutes == 120
    assert settings.horizon_days == 14
    assert settings.default_duration_minutes == 60
    assert settings.min_duration_minutes == 30
    assert settings.max_duration_minutes == 720
    settings.validate()

    assert get_planning_settings(None) == DEFAULT_PLANNING_SETTINGS

    with pytest.raises(ValueError, match="min_lead_minutes_must_be_positive"):
        PlanningSettings(min_lead_minutes=0).validate()

    with pytest.raises(ValueError, match="horizon_days_must_be_positive"):
        PlanningSettings(horizon_days=-1).validate()

    with pytest.raises(ValueError, match="duration_bounds_inconsistent"):
        PlanningSettings(
            min_duration_minutes=100, default_duration_minutes=50
        ).validate()

    with pytest.raises(ValueError, match="duration_bounds_inconsistent"):
        PlanningSettings(
            default_duration_minutes=800, max_duration_minutes=720
        ).validate()


def test_planning_settings_serialization() -> None:
    data = {
        "min_lead_minutes": 60,
        "horizon_days": 7,
        "default_duration_minutes": 45,
        "min_duration_minutes": 15,
        "max_duration_minutes": 360,
    }
    obj = PlanningSettings.from_dict(data)
    assert obj.min_lead_minutes == 60
    assert obj.horizon_days == 7
    assert obj.default_duration_minutes == 45
    assert obj.to_dict() == data


def test_schedule_item_validation() -> None:
    valid = ScheduleItem(
        weekday=1,
        start_time=time(9, 0),
        end_time=time(18, 0),
        timezone="Europe/Moscow",
    )
    assert valid.weekday == 1

    # Invalid weekday
    with pytest.raises(ValidationError):
        ScheduleItem(
            weekday=8,
            start_time=time(9, 0),
            end_time=time(18, 0),
            timezone="UTC",
        )

    # Start >= End
    with pytest.raises(ValidationError, match="schedule_start_must_precede_end"):
        ScheduleItem(
            weekday=1,
            start_time=time(18, 0),
            end_time=time(9, 0),
            timezone="UTC",
        )

    # Invalid timezone
    with pytest.raises(ValidationError, match="invalid_timezone"):
        ScheduleItem(
            weekday=1,
            start_time=time(9, 0),
            end_time=time(18, 0),
            timezone="NonExistent/Zone",
        )

    # Validity range invalid
    with pytest.raises(ValidationError, match="schedule_validity_range_invalid"):
        ScheduleItem(
            weekday=1,
            start_time=time(9, 0),
            end_time=time(18, 0),
            timezone="UTC",
            valid_from=date(2026, 12, 31),
            valid_to=date(2026, 1, 1),
        )


def test_schedule_overlap_detection() -> None:
    s1 = ScheduleItem(
        weekday=1,
        start_time=time(9, 0),
        end_time=time(13, 0),
        timezone="UTC",
    )
    s2 = ScheduleItem(
        weekday=1,
        start_time=time(13, 0),
        end_time=time(18, 0),
        timezone="UTC",
    )
    # Consecutive intervals do not overlap
    _check_schedule_overlaps([s1, s2])

    # Overlapping interval on same day raises 422
    s3 = ScheduleItem(
        weekday=1,
        start_time=time(12, 0),
        end_time=time(15, 0),
        timezone="UTC",
    )
    with pytest.raises(HTTPException) as exc:
        _check_schedule_overlaps([s1, s3])
    assert exc.value.status_code == 422
    assert exc.value.detail == "schedule_intervals_overlap"

    # Overlapping time but non-overlapping validity date ranges do not conflict
    s4 = ScheduleItem(
        weekday=1,
        start_time=time(9, 0),
        end_time=time(18, 0),
        timezone="UTC",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 5, 31),
    )
    s5 = ScheduleItem(
        weekday=1,
        start_time=time(9, 0),
        end_time=time(18, 0),
        timezone="UTC",
        valid_from=date(2026, 6, 1),
        valid_to=date(2026, 12, 31),
    )
    _check_schedule_overlaps([s4, s5])


def test_absence_request_validation() -> None:
    now = datetime.now(UTC)
    # Valid
    req = AbsenceCreateRequest(
        user_id=10,
        start_at=now,
        end_at=now + timedelta(hours=2),
        reason="vacation",
    )
    assert req.user_id == 10

    # Start >= end
    with pytest.raises(ValidationError, match="start_at_must_be_before_end_at"):
        AbsenceCreateRequest(
            user_id=10,
            start_at=now + timedelta(hours=2),
            end_at=now,
        )

    # Naive datetime
    with pytest.raises(ValidationError, match="datetime_must_be_timezone_aware"):
        AbsenceCreateRequest(
            user_id=10,
            start_at=datetime(2026, 5, 1, 10, 0),
            end_at=datetime(2026, 5, 1, 12, 0),
        )


def test_distribution_membership_update_requires_comment() -> None:
    # Non-empty comment is valid
    req = DistributionMembershipUpdateRequest(
        pool_code=1,
        enabled=True,
        comment="Руководитель утвердил",
    )
    assert req.comment == "Руководитель утвердил"

    # Empty / whitespace comment is rejected
    with pytest.raises(ValidationError, match="comment_required"):
        DistributionMembershipUpdateRequest(
            pool_code=1,
            enabled=True,
            comment="   ",
        )


def test_planning_settings_update_request_validation() -> None:
    valid = PlanningSettingsUpdateRequest(
        min_lead_minutes=60,
        horizon_days=30,
        default_duration_minutes=45,
        min_duration_minutes=15,
        max_duration_minutes=120,
    )
    assert valid.min_lead_minutes == 60

    with pytest.raises(ValidationError, match="duration_bounds_inconsistent"):
        PlanningSettingsUpdateRequest(
            min_duration_minutes=60,
            default_duration_minutes=30,
            max_duration_minutes=120,
        )


def test_planning_settings_applied_to_window_validations() -> None:
    custom_settings = PlanningSettings(min_lead_minutes=60, horizon_days=5)
    now = datetime.now(UTC)

    # With default 120 min lead, 90 min ahead fails
    with pytest.raises(ValueError, match="planned_start_too_soon"):
        validate_manager_window(now + timedelta(minutes=90), 60)

    # With custom 60 min lead, 90 min ahead passes
    validate_manager_window(now + timedelta(minutes=90), 60, settings=custom_settings)

    # With default 14 day horizon, 7 days ahead passes
    validate_manager_window(now + timedelta(days=7), 60)

    # With custom 5 day horizon, 7 days ahead fails
    with pytest.raises(ValueError, match="planned_start_too_far"):
        validate_manager_window(now + timedelta(days=7), 60, settings=custom_settings)

    # Role create policy also uses custom settings
    with pytest.raises(ValueError, match="planned_start_too_far"):
        validate_role_create(
            scenario=CreateScenario.L1,
            planned_start_at=now + timedelta(days=7),
            planned_duration_minutes=60,
            now=now,
            settings=custom_settings,
        )

    # Reschedule window also uses custom settings
    with pytest.raises(ValueError, match="planned_start_too_far"):
        validate_reschedule_window(
            planned_start_at=now + timedelta(days=7),
            urgency_code=0,
            now=now,
            settings=custom_settings,
        )


def test_day_shifts_request_validation() -> None:
    from app.api.admin_planning import DayShiftsReplaceRequest

    ok = DayShiftsReplaceRequest(
        date_from=date(2026, 10, 1),
        date_to=date(2026, 10, 31),
        timezone="Asia/Yekaterinburg",
        days=[{"day": "2026-10-05", "start_time": "07:00", "end_time": "16:00"}],
    )
    assert ok.days[0].day == date(2026, 10, 5)

    with pytest.raises(ValidationError):
        DayShiftsReplaceRequest(
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 31),
            timezone="Asia/Yekaterinburg",
            days=[{"day": "2026-10-05", "start_time": "16:00", "end_time": "07:00"}],
        )
    with pytest.raises(ValidationError):
        DayShiftsReplaceRequest(
            date_from=date(2026, 10, 31),
            date_to=date(2026, 10, 1),
            timezone="Asia/Yekaterinburg",
            days=[],
        )
    with pytest.raises(ValidationError):
        DayShiftsReplaceRequest(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 12, 31),
            timezone="Asia/Yekaterinburg",
            days=[],
        )
    with pytest.raises(ValidationError):
        DayShiftsReplaceRequest(
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 2),
            timezone="Mars/Phobos",
            days=[],
        )


def test_replace_day_shifts_rejects_bad_days_before_touching_the_database() -> None:
    from app.admin.repository import AdministrativeRepository, DayShift

    repo = AdministrativeRepository(connection=None)  # type: ignore[arg-type]

    def shift(day: date, start: str = "07:00", end: str = "16:00") -> DayShift:
        return DayShift(
            1, day, time.fromisoformat(start), time.fromisoformat(end), "UTC"
        )

    kwargs = {
        "user_id": 1,
        "date_from": date(2026, 10, 1),
        "date_to": date(2026, 10, 7),
    }
    with pytest.raises(ValueError, match="schedule_day_out_of_range"):
        repo.replace_day_shifts(
            **kwargs, shifts=[shift(date(2026, 10, 9))], actor_user_id=1
        )
    with pytest.raises(ValueError, match="schedule_duplicate_day"):
        repo.replace_day_shifts(
            **kwargs,
            shifts=[shift(date(2026, 10, 2)), shift(date(2026, 10, 2), "08:00")],
            actor_user_id=1,
        )
    with pytest.raises(ValueError, match="schedule_start_must_precede_end"):
        repo.replace_day_shifts(
            **kwargs,
            shifts=[shift(date(2026, 10, 2), "16:00", "07:00")],
            actor_user_id=1,
        )
