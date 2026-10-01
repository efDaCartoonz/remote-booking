from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.admin.planning_settings import PlanningSettings, get_planning_settings
from app.admin.repository import AdministrativeRepository, DayShift, WorkSchedule
from app.auth.dependencies import require_roles
from app.auth.store import UserAuthRecord
from app.cards.constants import RoleId
from app.db import db_connection

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

require_admin_role = require_roles(int(RoleId.ADMIN))
require_admin_or_manager = require_roles(int(RoleId.ADMIN), int(RoleId.MANAGER))


# ---------------------------------------------------------------------------
# 1. Schedules
# ---------------------------------------------------------------------------


class ScheduleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=1, le=7)
    start_time: time
    end_time: time
    timezone: str
    valid_from: date | None = None
    valid_to: date | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, Exception):
            raise ValueError("invalid_timezone")
        return value

    @model_validator(mode="after")
    def validate_times_and_dates(self) -> ScheduleItem:
        if self.start_time >= self.end_time:
            raise ValueError("schedule_start_must_precede_end")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_from > self.valid_to
        ):
            raise ValueError("schedule_validity_range_invalid")
        return self


def _check_schedule_overlaps(items: list[ScheduleItem]) -> None:
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            s1, s2 = items[i], items[j]
            if s1.weekday != s2.weekday:
                continue
            dates_overlap = (
                s1.valid_from is None
                or s2.valid_to is None
                or s1.valid_from <= s2.valid_to
            ) and (
                s1.valid_to is None
                or s2.valid_from is None
                or s1.valid_to >= s2.valid_from
            )
            times_overlap = max(s1.start_time, s2.start_time) < min(
                s1.end_time, s2.end_time
            )
            if dates_overlap and times_overlap:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="schedule_intervals_overlap",
                )


MAX_SCHEDULE_RANGE_DAYS = 93


class DayShiftItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: date
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def validate_times(self) -> DayShiftItem:
        if self.start_time >= self.end_time:
            raise ValueError("schedule_start_must_precede_end")
        return self


class DayShiftsReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date
    date_to: date
    timezone: str
    days: list[DayShiftItem]

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, Exception):
            raise ValueError("invalid_timezone")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> DayShiftsReplaceRequest:
        if self.date_from > self.date_to:
            raise ValueError("schedule_validity_range_invalid")
        if (self.date_to - self.date_from).days + 1 > MAX_SCHEDULE_RANGE_DAYS:
            raise ValueError("schedule_range_too_long")
        return self


class UserDayShiftsResponse(BaseModel):
    user_id: int
    timezone: str | None
    days: list[DayShiftItem]


class DayShiftsListResponse(BaseModel):
    date_from: date
    date_to: date
    users: list[UserDayShiftsResponse]


def _validate_range(date_from: date, date_to: date) -> None:
    if date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="schedule_validity_range_invalid",
        )
    if (date_to - date_from).days + 1 > MAX_SCHEDULE_RANGE_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="schedule_range_too_long",
        )


class ScheduleEmployee(BaseModel):
    id: int
    full_name: str
    timezone: str
    roles: list[int]


@router.get("/schedules/employees", response_model=list[ScheduleEmployee])
def list_schedule_employees(
    _: Annotated[UserAuthRecord, Depends(require_admin_or_manager)],
) -> list[ScheduleEmployee]:
    with db_connection() as connection:
        rows = AdministrativeRepository(connection).list_schedule_employees()
    return [ScheduleEmployee(**row) for row in rows]


@router.get("/schedules/days", response_model=DayShiftsListResponse)
def list_day_shifts(
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    _: Annotated[UserAuthRecord, Depends(require_admin_or_manager)],
) -> DayShiftsListResponse:
    _validate_range(date_from, date_to)
    with db_connection() as connection:
        shifts = AdministrativeRepository(connection).list_day_shifts(
            date_from=date_from, date_to=date_to
        )
    grouped: dict[int, UserDayShiftsResponse] = {}
    for shift in shifts:
        entry = grouped.setdefault(
            shift.user_id,
            UserDayShiftsResponse(
                user_id=shift.user_id, timezone=shift.timezone, days=[]
            ),
        )
        entry.days.append(
            DayShiftItem(
                day=shift.work_date,
                start_time=shift.start_time,
                end_time=shift.end_time,
            )
        )
    return DayShiftsListResponse(
        date_from=date_from, date_to=date_to, users=list(grouped.values())
    )


@router.put("/schedules/{user_id}/days", response_model=UserDayShiftsResponse)
def replace_day_shifts(
    user_id: Annotated[int, Path(gt=0)],
    payload: DayShiftsReplaceRequest,
    user: Annotated[UserAuthRecord, Depends(require_admin_or_manager)],
) -> UserDayShiftsResponse:
    shifts = [
        DayShift(
            user_id=user_id,
            work_date=item.day,
            start_time=item.start_time,
            end_time=item.end_time,
            timezone=payload.timezone,
        )
        for item in payload.days
    ]
    with db_connection() as connection:
        repo = AdministrativeRepository(connection)
        try:
            repo.replace_day_shifts(
                user_id=user_id,
                date_from=payload.date_from,
                date_to=payload.date_to,
                shifts=shifts,
                actor_user_id=user.id,
            )
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                status_code=(
                    status.HTTP_404_NOT_FOUND
                    if code == "user_not_found"
                    else status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
                detail=code,
            ) from exc
    return UserDayShiftsResponse(
        user_id=user_id,
        timezone=payload.timezone,
        days=sorted(payload.days, key=lambda item: item.day),
    )


@router.get("/schedules/{user_id}", response_model=list[ScheduleItem])
def get_user_schedules(
    user_id: Annotated[int, Path(gt=0)],
    _: Annotated[UserAuthRecord, Depends(require_admin_or_manager)],
) -> list[ScheduleItem]:
    with db_connection() as connection:
        repo = AdministrativeRepository(connection)
        schedules = repo.get_schedules(user_id)
        return [
            ScheduleItem(
                weekday=s.weekday,
                start_time=s.start_time,
                end_time=s.end_time,
                timezone=s.timezone,
                valid_from=s.valid_from,
                valid_to=s.valid_to,
            )
            for s in schedules
        ]


@router.put("/schedules/{user_id}", response_model=list[ScheduleItem])
def replace_user_schedules(
    user_id: Annotated[int, Path(gt=0)],
    payload: list[ScheduleItem],
    user: Annotated[UserAuthRecord, Depends(require_admin_role)],
) -> list[ScheduleItem]:
    _check_schedule_overlaps(payload)
    work_schedules = [
        WorkSchedule(
            weekday=item.weekday,
            start_time=item.start_time,
            end_time=item.end_time,
            timezone=item.timezone,
            valid_from=item.valid_from,
            valid_to=item.valid_to,
        )
        for item in payload
    ]
    with db_connection() as connection:
        repo = AdministrativeRepository(connection)
        try:
            repo.replace_schedules(
                user_id=user_id, schedules=work_schedules, actor_user_id=user.id
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
    return payload


# ---------------------------------------------------------------------------
# 2. Absences
# ---------------------------------------------------------------------------


class AbsenceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)
    start_at: datetime
    end_at: datetime
    reason: str | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def validate_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime_must_be_timezone_aware")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> AbsenceCreateRequest:
        if self.start_at >= self.end_at:
            raise ValueError("start_at_must_be_before_end_at")
        return self


class AbsenceResponse(BaseModel):
    id: int
    user_id: int
    start_at: datetime
    end_at: datetime
    reason: str | None = None
    created_by_id: int | None = None
    created_at: datetime | None = None


class AbsenceDeleteResponse(BaseModel):
    deleted: bool
    id: int


@router.get("/absences", response_model=list[AbsenceResponse])
def list_absences(
    _: Annotated[UserAuthRecord, Depends(require_admin_or_manager)],
    user_id: Annotated[int | None, Query(gt=0)] = None,
    period_from: Annotated[datetime | None, Query()] = None,
    period_to: Annotated[datetime | None, Query()] = None,
) -> list[AbsenceResponse]:
    if period_from is not None and (
        period_from.tzinfo is None or period_from.utcoffset() is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_from_must_be_timezone_aware",
        )
    if period_to is not None and (
        period_to.tzinfo is None or period_to.utcoffset() is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_to_must_be_timezone_aware",
        )
    if period_from is not None and period_to is not None and period_from >= period_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_from_must_be_before_period_to",
        )
    with db_connection() as connection:
        repo = AdministrativeRepository(connection)
        rows = repo.list_absences(
            user_id=user_id, start_at=period_from, end_at=period_to
        )
        return [AbsenceResponse(**row) for row in rows]


@router.post(
    "/absences",
    response_model=AbsenceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_absence(
    payload: AbsenceCreateRequest,
    user: Annotated[UserAuthRecord, Depends(require_admin_role)],
) -> AbsenceResponse:
    with db_connection() as connection:
        repo = AdministrativeRepository(connection)
        absence_id = repo.add_absence(
            user_id=payload.user_id,
            start_at=payload.start_at,
            end_at=payload.end_at,
            actor_user_id=user.id,
            reason=payload.reason,
        )
        row = repo.get_absence_by_id(absence_id)
        if row is None:
            return AbsenceResponse(
                id=absence_id,
                user_id=payload.user_id,
                start_at=payload.start_at,
                end_at=payload.end_at,
                reason=payload.reason,
                created_by_id=user.id,
                created_at=None,
            )
        return AbsenceResponse(**row)


@router.delete("/absences/{absence_id}", response_model=AbsenceDeleteResponse)
def delete_absence(
    absence_id: Annotated[int, Path(gt=0)],
    user: Annotated[UserAuthRecord, Depends(require_admin_role)],
) -> AbsenceDeleteResponse:
    with db_connection() as connection:
        repo = AdministrativeRepository(connection)
        deleted = repo.delete_absence(absence_id=absence_id, actor_user_id=user.id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="absence_not_found",
            )
    return AbsenceDeleteResponse(deleted=True, id=absence_id)


# ---------------------------------------------------------------------------
# 3. Production Calendar
# ---------------------------------------------------------------------------


class CalendarDayResponse(BaseModel):
    date: date
    day_type_code: int
    is_manual_override: bool
    updated_by_id: int | None = None
    updated_at: datetime | None = None
    comment: str | None = None


class CalendarDayUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_type_code: int = Field(ge=0, le=2)
    is_manual_override: bool = True
    comment: str | None = None


@router.get("/calendar/days", response_model=list[CalendarDayResponse])
def get_calendar_days(
    _: Annotated[UserAuthRecord, Depends(require_admin_or_manager)],
    from_date: Annotated[date | None, Query(alias="from_date")] = None,
    to_date: Annotated[date | None, Query(alias="to_date")] = None,
    period_from: Annotated[date | None, Query(alias="period_from")] = None,
    period_to: Annotated[date | None, Query(alias="period_to")] = None,
) -> list[CalendarDayResponse]:
    start = from_date or period_from
    end = to_date or period_to
    if start is None or end is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date_range_required",
        )
    if start > end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from_date_must_be_before_or_equal_to_date",
        )
    with db_connection() as connection:
        repo = AdministrativeRepository(connection)
        rows = repo.list_calendar_days(start_date=start, end_date=end)
        return [CalendarDayResponse(**row) for row in rows]


@router.put("/calendar/days/{calendar_date}", response_model=CalendarDayResponse)
def update_calendar_day(
    calendar_date: date,
    payload: CalendarDayUpdateRequest,
    user: Annotated[UserAuthRecord, Depends(require_admin_role)],
) -> CalendarDayResponse:
    with db_connection() as connection:
        repo = AdministrativeRepository(connection)
        repo.set_calendar_day(
            calendar_date=calendar_date,
            day_type_code=payload.day_type_code,
            is_manual_override=payload.is_manual_override,
            comment=payload.comment,
            actor_user_id=user.id,
        )
        row = repo.get_calendar_day(calendar_date)
        if row is None:
            return CalendarDayResponse(
                date=calendar_date,
                day_type_code=payload.day_type_code,
                is_manual_override=payload.is_manual_override,
                comment=payload.comment,
                updated_by_id=user.id,
                updated_at=None,
            )
        return CalendarDayResponse(**row)


# ---------------------------------------------------------------------------
# 4. Distribution Membership
# ---------------------------------------------------------------------------


class DistributionMemberResponse(BaseModel):
    id: int
    user_id: int
    pool_code: int
    is_enabled: bool
    enabled_by_id: int | None = None
    enabled_at: datetime | None = None
    disabled_by_id: int | None = None
    disabled_at: datetime | None = None
    comment: str | None = None


class DistributionMembershipUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pool_code: int = Field(ge=1, le=2)
    enabled: bool
    comment: str = Field(min_length=1)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("comment_required")
        return value.strip()


@router.get("/distribution/members", response_model=list[DistributionMemberResponse])
def list_distribution_members(
    _: Annotated[UserAuthRecord, Depends(require_admin_or_manager)],
    pool_code: Annotated[int | None, Query(ge=1, le=2)] = None,
) -> list[DistributionMemberResponse]:
    with db_connection() as connection:
        repo = AdministrativeRepository(connection)
        rows = repo.list_distribution_members(pool_code=pool_code)
        return [DistributionMemberResponse(**row) for row in rows]


@router.put(
    "/distribution/members/{user_id}", response_model=DistributionMemberResponse
)
def update_distribution_membership(
    user_id: Annotated[int, Path(gt=0)],
    payload: DistributionMembershipUpdateRequest,
    user: Annotated[UserAuthRecord, Depends(require_admin_role)],
) -> DistributionMemberResponse:
    with db_connection() as connection:
        repo = AdministrativeRepository(connection)
        repo.set_distribution_membership(
            user_id=user_id,
            pool_code=payload.pool_code,
            enabled=payload.enabled,
            actor_user_id=user.id,
            comment=payload.comment,
        )
        row = repo.get_distribution_membership(
            user_id=user_id, pool_code=payload.pool_code
        )
        if row is None:
            return DistributionMemberResponse(
                id=0,
                user_id=user_id,
                pool_code=payload.pool_code,
                is_enabled=payload.enabled,
                comment=payload.comment,
            )
        return DistributionMemberResponse(**row)


# ---------------------------------------------------------------------------
# 5. Planning Settings (ADM-010)
# ---------------------------------------------------------------------------


class PlanningSettingsResponse(BaseModel):
    min_lead_minutes: int
    horizon_days: int
    default_duration_minutes: int
    min_duration_minutes: int
    max_duration_minutes: int


class PlanningSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_lead_minutes: int = Field(default=120, gt=0)
    horizon_days: int = Field(default=14, gt=0)
    default_duration_minutes: int = Field(default=60, gt=0)
    min_duration_minutes: int = Field(default=30, gt=0)
    max_duration_minutes: int = Field(default=720, gt=0)

    @model_validator(mode="after")
    def validate_consistency(self) -> PlanningSettingsUpdateRequest:
        if not (
            self.min_duration_minutes
            <= self.default_duration_minutes
            <= self.max_duration_minutes
        ):
            raise ValueError("duration_bounds_inconsistent")
        return self


@router.get("/settings/planning", response_model=PlanningSettingsResponse)
def get_admin_planning_settings(
    _: Annotated[UserAuthRecord, Depends(require_admin_or_manager)],
) -> PlanningSettingsResponse:
    with db_connection() as connection:
        repo = AdministrativeRepository(connection)
        settings_obj = get_planning_settings(repo)
        return PlanningSettingsResponse(
            min_lead_minutes=settings_obj.min_lead_minutes,
            horizon_days=settings_obj.horizon_days,
            default_duration_minutes=settings_obj.default_duration_minutes,
            min_duration_minutes=settings_obj.min_duration_minutes,
            max_duration_minutes=settings_obj.max_duration_minutes,
        )


@router.put("/settings/planning", response_model=PlanningSettingsResponse)
def update_admin_planning_settings(
    payload: PlanningSettingsUpdateRequest,
    user: Annotated[UserAuthRecord, Depends(require_admin_role)],
) -> PlanningSettingsResponse:
    settings_obj = PlanningSettings(
        min_lead_minutes=payload.min_lead_minutes,
        horizon_days=payload.horizon_days,
        default_duration_minutes=payload.default_duration_minutes,
        min_duration_minutes=payload.min_duration_minutes,
        max_duration_minutes=payload.max_duration_minutes,
    )
    with db_connection() as connection:
        repo = AdministrativeRepository(connection)
        repo.set_planning_settings(
            settings=settings_obj.to_dict(), actor_user_id=user.id
        )
    return PlanningSettingsResponse(
        min_lead_minutes=payload.min_lead_minutes,
        horizon_days=payload.horizon_days,
        default_duration_minutes=payload.default_duration_minutes,
        min_duration_minutes=payload.min_duration_minutes,
        max_duration_minutes=payload.max_duration_minutes,
    )
